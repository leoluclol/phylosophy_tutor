# Philosophy Tutor — V1

Un **tutor filosofico procedurale**: genera lezioni di filosofia partendo da un
curriculum predefinito, con ricerca bibliografica sul web, verifica critica del
materiale e produzione finale in Markdown.

Non è un chatbot: nessuna interfaccia, nessuna memoria, nessun database.
È una pipeline Python che esegui a mano:

```bash
python generate.py --day 1
# → lezioni/001.md
```

---

## 1. Che cosa fa

Una pipeline a fasi, ciascuna con output strutturato e validato (Pydantic):

```
curriculum/property.yaml
       │
       ▼
generate.py                         CLI (--day N, --force)
       │
       ├─ RESEARCHER                ricerca sul web + lettura pagine + estrazione passaggi
       │      └─ research.json      (.cache/research/day_XXX.json)
       ├─ CRITIC                    revisione scettica di ricerca e attribuzioni
       │      └─ critique.json      (.cache/critique/day_XXX.json)
       ├─ WRITER                    scrive la lezione Markdown
       └─ lezioni/001.md
```

- **Researcher** formula query, fa ricerche web, scarica le pagine, estrae il
  testo, e produce `research.json` con fonti primarie/secondarie, passaggi
  **verificati verbatim** e incertezze. Un controllo meccanico ricontrolla ogni
  passaggio "verified": se il testo non compare davvero nelle pagine scaricate,
  viene declassato a "unverified".
- **Critic** revisiona come un revisore scettico: citazioni, attribuzioni,
  strawman, anacronismi, semplificazioni, fonti deboli, simmetrie false e
  bias di conferma.
- **Writer** scrive la lezione **solo** dal materiale verificato, con regole
  stringenti anti-confirmation-bias (tesi → migliore obiezione → risposta →
  problema residuo), e un **ciclo di autocorrezione** alimentato da controlli
  meccanici (le citazioni presenti nella lezione devono combaciare con i
  passaggi verificati; niente citazioni inventate; niente "name-dropping"
  senza supporto).

Ogni lezione dura ~90 minuti (2800–3600 parole) e segue una struttura fissa
(Domanda fondamentale, Obiettivi, Contesto, Fonte primaria, Spiegazione,
Argomento principale, Obiezione più forte, Possibile risposta, Dove rimane il
problema, Confronto, Domande per riflettere, Esercizio, Fonti).

Il corso del primo mese ruota intorno alla domanda **"Che cosa possiamo
possedere?"** e NON prende posizione a favore o contro la proprietà privata:
mette lo studente davanti ai migliori argomenti di Locke, Marx, Nozick, Cohen,
Rawls, delle teorie dei commons e delle teorie istituzionali.

---

## 2. Installazione

Requisiti: Python 3.10+.

```bash
cd phylosophy_tutor
python3 -m venv .venv
source .venv/bin/activate            # (o .venv\Scripts\activate su Windows)
pip install -r requirements.txt
```

---

## 3. Configurare le API key

Copia `.env.example` in `.env` e inserisci la chiave OpenRouter:

```bash
cp .env.example .env
# apri .env e imposta OPENROUTER_API_KEY=sk-or-...
```

Il file `.env` non viene mai committato (vedi `.gitignore`). Le chiavi sono
lette dall'ambiente al momento della chiamata e non vengono mai stampate nei
log né salvate nei file cache.

Provider LLM supportato in V1: **OpenRouter** (endpoint Chat Completions).
Il nome della variabile d'ambiente è configurabile (`llm.api_key_env`).

---

## 4. Search provider

La ricerca web è astratta dietro un'interfaccia a `src/search.py`:

```python
class SearchProvider:
    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        ...
```

Provider disponibili in V1:

| valore     | implementazione                          |
|------------|------------------------------------------|
| `duckduckgo` | HTML endpoint di DuckDuckGo (default)   |
| `bing`       | scraping dei risultati di Bing          |
| `multi`      | tenta più provider e unisce i risultati |

Impostazione in `config.yaml`:

```yaml
search:
  enabled: true
  provider: duckduckgo
  max_results: 10
  max_fetch: 8        # pagine scaricabili per lezione
  max_page_chars: 12000
```

Oltre alla ricerca, il researcher usa sempre un **registro di fonti
deterministiche manualmente verificate** (`src/sources.py` + le `known_*_sources`
del curriculum): SEP, IEP, Gutenberg, Early Modern Texts, Marxists Internet
Archive. Questo garantisce che anche in reti in cui i motori di ricerca
bloccano lo scraping la pipeline produca comunque fonti primarie di qualità.

Nota: la "ricerca" è **separata** dal ragionamento dell'LLM. Il modello non
elabora pagine che non siano state davvero recuperate: se una pagina non è
accessibile, il sistema dichiara `source_status = "unavailable"` e non finge di
averla letta.

---

## 5. Configurare il modello

Ogni ruolo usa un modello e parametri propri in `config.yaml`:

```yaml
llm:
  provider: openrouter
  researcher:
    model: openai/gpt-5.4-mini
    temperature: 0.3
    max_tokens: 16000
  critic:
    model: openai/gpt-5.4-mini
    temperature: 0.2
    max_tokens: 12000
  writer:
    model: openai/gpt-5.4-mini
    temperature: 0.6
    max_tokens: 16000
  max_json_retries: 2      # tentativi di riparazione di JSON non valido
  max_writer_attempts: 2   # autocorrezioni del writer sui controlli di integrità
```

Se l'output JSON di una fase non è valido, il sistema tenta di correggerlo
rigenerando (fino a `max_json_retries`), poi fallisce con un errore leggibile.
Se il writer non riesce a superare i controlli di integrità dopo
`max_writer_attempts`, la lezione viene comunque salvata **con warning espliciti
a video** (mai silenziosamente). Con modelli più forti (es. `anthropic/claude-sonnet-4-…`,
`openai/gpt-4o`) questi avvisi sono molto più rari.

---

## 6. Eseguire una lezione

```bash
python generate.py --day 1
python generate.py --day 15
python generate.py --day 30 --force
```

Output tipico:

```
[1/5] Loading curriculum...
[2/5] Searching sources...
[3/5] Running researcher...
[4/5] Running critic...
[5/5] Running writer...
Saved lessons/001.md
```

Opzioni:

| parametro         | effetto                                                      |
|-------------------|--------------------------------------------------------------|
| `--day N`         | giorno da generare (1..30) — **obbligatorio**                |
| `--force`         | rigenera tutto ignorando cache (vedi §8)                    |
| `--config path`   | path di `config.yaml`                                        |
| `--env path`      | path del file `.env`                                         |
| `--provider name` | override del search provider (duckduckgo\|bing\|multi)      |
| `--debug`         | log verbosi                                                 |

La generazione è **deterministica per giorno**: `--day 7` non dipende dai
giorni 1–6. Nessuna memoria, nessuno stato studente.

---

## 7. Dove viene salvato l'output

- `lessons/001.md`, `lessons/015.md`, … file nome `giorno a 3 cifre`.
- Intermedi:
  - `.cache/research/day_001.json`
  - `.cache/critique/day_001.json`
  - `.cache/search/…` (risultati di ricerca)
  - `.cache/pages/…` (testo delle pagine scaricate)

La cartella `lessons/` e `.cache/` sono ignorate da git.

---

## 8. Forzare una rigenerazione

Se `lessons/001.md` esiste già, il comando **non sovrascrive**:

```bash
python generate.py --day 1
# lessons/001.md already exists — skipping (use --force to regenerate)
```

Per rigenerare da zero (ignorando tutti i file cache):

```bash
python generate.py --day 1 --force
```

Per rigenerare **solo la lezione** (riusando ricerca+critica già in cache),
cancella solo il file della lezione:

```bash
rm lessons/001.md
python generate.py --day 1
```

---

## 9. Cambiare il curriculum

Il curriculum del mese è in `curriculum/property.yaml`: 30 voci con
`day`, `title`, `fundamental_question`, `why_it_matters`, `objectives`,
`concepts`, `authors`, `known_primary_sources`, `known_secondary_sources`,
`suggested_queries` e `requirements`.

- Per modificare una lezione: edita la voce corrispondente.
- Per un mese diverso: crea `curriculum/<mese>.yaml` con la stessa struttura e
  punta `curriculum.file` (e `curriculum.month`) in `config.yaml` su di esso.

Regole pratiche per le fonti note:
- fonti primarie di pubblico dominio: aggiungi un URL verificato
  (Gutenberg, Early Modern Texts, Marxists Internet Archive, ecc.).
- opere sotto copyright (Nozick, Cohen, Rawls, ecc.): **lascia `url` vuota**.
  Il researcher non inventerà un link; la lezione userà fonti secondarie
  e dichiarerà la mancanza di una citazione primaria verificata.

---

## 10. Test

```bash
source .venv/bin/activate
python -m unittest discover -s tests -v
```

I test coprono: struttura del curriculum (30 giorni, campi obbligatori),
schemi Pydantic, costruzione della config, generazione end-to-end con un
LLM finto (file creato, nome file corretto), nessuna sovrascrittura senza
`--force`, riuso della cache, controlli di integrità e **nessuna chiave API
nei log**.

---

## 11. Architettura e anti-bias (in breve)

- Ogni fase produce JSON validato con Pydantic (`src/models.py`); niente testo
  libero passato tra i modelli.
- Il researcher distingue fonti PRIMARIE / SECONDARIE / TERTIARIE e passaggi
  `verified` / `unverified` (con verifica meccanica anti-fabbricazione).
- Il writer applica la struttura anti-confirmation-bias:
  `TESI → migliore formulazione → migliore obiezione → risposta → problema residuo`.
- Nei luoghi in cui i controlli meccanici falliscono o producono incertezze, il
  sistema stampa `WARNING` espliciti e non tace mai.

## 12. Limitazioni note (V1)

- Un solo mese (30 giorni) nel curriculum.
- Nessun feedback utente, DB, memoria, RAG, web UI o autenticazione
  (volutamente fuori scope per V1).
- PDF non estratti (le pagine PDF sono marcate `source_status = "unavailable"`).
- La qualità delle citazioni dipende dal modello writer: con modelli piccoli
  possono comparire avvisi di integrità; aumentare la qualità del modello o
  dei prompt (in `prompts/`) per ridurli.

## 13. Invio quotidiano su Telegram

Oltre alla generazione manuale, il progetto può consegnare ogni giorno la
lezione **successiva** del curriculum su Telegram via cron:

- `send_daily.py` — genera la lezione del giorno, converte il Markdown in HTML
  compatibile con Telegram (rispettando il limite di 4096 caratteri/messaggio)
  e la invia al bot.
- `daily_lesson.sh` — wrapper per cron: usa il venv del progetto (cron ha un
  PATH minimo), si sposta nella root del progetto e scrive i log in `logs/`.
- `crontab.example` — file di crontab con la riga
  `0 5 * * * /var/www/phylosophy_tutor/daily_lesson.sh` (ogni giorno alle 05:00
  ora del server, cioè 07:00 nel fuso dell'utente).

Configurazione: metti `TELEGRAM_BOT_TOKEN` e `TELEGRAM_USER_ID` nel `.env`
(vedi `.env.example`; il token si crea con @BotFather, l'id utente con
@userinfobot). Il giorno inviato viene ricordato in `.cache/daily_state.json`;
`--day N` forza un giorno specifico, `--loop` fa ripartire il corso dal
giorno 1 a fine mese:

```bash
python send_daily.py --day 1 --dry-run   # genera + formatta senza inviare
python send_daily.py --loop              # prossima lezione, poi invia
```

Per installare la riga cron: `crontab crontab.example` (sostituisce l'intera
crontab) oppure appendila alla tua crontab con `crontab -e`.
