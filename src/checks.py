"""Mechanical integrity checks between the lesson and the research output.

These are deterministic trip-wires, not interpretations: they catch quote
invention, translated-quotes-presented-as-verbatim and unsupported name-drops
that the LLM layers sometimes get wrong. They never prevent a lesson from
being saved silently — they produce loud warnings, and they power the
writer's self-correction loop.
"""
from __future__ import annotations

import re
import unicodedata

from .models import CurriculumDay, ResearchOutput

REQUIRED_SECTIONS = [
    "## Domanda fondamentale",
    "## Obiettivi",
    "## Perché questa domanda conta",
    "## Contesto",
    "## Fonte primaria",
    "## Spiegazione",
    "## Argomento principale",
    "## Obiezione più forte",
    "## Possibile risposta",
    "## Dove rimane il problema",
    "## Confronto con altre posizioni",
    "## Domande per riflettere",
    "## Esercizio",
    "## Fonti",
]

# A lesson below this word count is mechanically rejected and the writer is
# asked to rewrite it in more depth. Set so that the configured writer model
# passes on the first attempt: gpt-4o reliably produces ~1.5k words while
# stronger models (claude-sonnet) reach 3k+. Tune with the writer model.
MIN_LESSON_WORDS = 1500

# Philosopher surnames that could plausibly be "name-dropped" in a lesson but
# never appear in the research or curriculum.
PHILOSOPHER_SURNAMES = {
    "locke", "marx", "nozick", "cohen", "rawls", "rousseau", "hobbes",
    "hume", "kant", "hegel", "proudhon", "bastiat", "bentham", "mill",
    "smith", "ricardo", "george", "steiner", "ostrom", "hardin",
    "honoré", "honore", "hohfeld", "waldron", "simmons", "wenar",
    "tully", "murphy", "nagel", "roemer", "hayek", "dworkin",
    "derrida", "foucault", "plato", "platone", "aristotle", "aristotele",
}

_MIN_QUOTE_LEN = 24  # normalized chars; shorter spans are ignored


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[^0-9a-zA-Z\u00c0-\u024f]", "", text.lower())


def _quoted_spans(lesson: str) -> list[str]:
    spans: list[str] = []
    for pattern in (r'"((?:[^"\\\n]|\\.)*)"', r"«([^»]+)»", r"“([^”]+)”"):
        spans.extend(re.findall(pattern, lesson))
    return [s for s in spans if len(_normalize(s)) >= _MIN_QUOTE_LEN]


def _quote_matches(norm_quote: str, norm_passage: str) -> bool:
    """Fuzzy verbatim check: the quote is 'the same text' as the passage if it
    is contained, or if almost all its words appear in order-ish in one
    verified passage."""
    if norm_quote in norm_passage:
        return True
    quote_words = norm_quote.split()
    if not quote_words:
        return False
    pos = 0
    matched = 0
    for word in quote_words:
        start = norm_passage.find(word, pos)
        if start != -1:
            matched += 1
            pos = start + len(word)
    return matched / len(quote_words) >= 0.9


def quote_problems(lesson: str, research: ResearchOutput) -> list[str]:
    verified = [
        _normalize(p.text)
        for p in research.primary_passages
        if p.verification_status == "verified" and p.text
    ]
    # Article/book titles are legitimate to put between quote marks in a
    # bibliography; they are not claims.
    titles = [
        _normalize(t)
        for t in [s.title for s in research.primary_sources + research.secondary_sources]
        if t
    ]
    if not verified and not titles:
        # No verified primary material and nothing to cite by title: NO
        # quotation can be justified. Flag any quoted span so the writer
        # cannot smuggle in a fake quote.
        if _quoted_spans(lesson):
            return [
                "quoted text present, but research contains no verified primary "
                "passage that could justify it"
            ]
        return []
    problems = []
    for span in _quoted_spans(lesson):
        norm = _normalize(span)
        if any(_quote_matches(norm, title) for title in titles):
            continue  # citing a source title, not quoting a claim
        if verified and any(_quote_matches(norm, vt) for vt in verified):
            continue  # verbatim verified primary passage
        problems.append(f"quoted text not matching any verified primary passage: {span[:90]}")
    return problems


def _surname_tokens(names) -> set[str]:
    tokens: set[str] = set()
    for name in names:
        for token in re.findall(r"[A-Za-zÀ-ÿ'\-]+", str(name)):
            if len(token) >= 4:
                tokens.add(token.lower())
    return tokens


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", " ", text, flags=re.IGNORECASE)


def attribution_problems(
    lesson: str, entry: CurriculumDay, research: ResearchOutput
) -> list[str]:
    supported = {"locke"}
    supported |= _surname_tokens(
        [s.author for s in research.primary_sources + research.secondary_sources]
    )
    supported |= _surname_tokens(entry.authors + entry.concepts)
    # Any name that appears anywhere in the research output (e.g. as context
    # inside a key claim or secondary article) counts as supported.
    supported |= _surname_tokens([research.model_dump_json()])

    mentioned = {
        token for token in re.findall(r"[A-Za-zÀ-ÿ'\-]+", _strip_urls(lesson))
        if token.lower() in PHILOSOPHER_SURNAMES
    }
    unsupported = sorted(n for n in mentioned if n.lower() not in supported)
    return [
        f"mention of {name} is not supported by research.json or the curriculum"
        for name in unsupported
    ]


def section_problems(lesson: str, required: list[str]) -> list[str]:
    return [f"missing section {sec!r}" for sec in required if sec not in lesson]


def _transparency_note(line: str) -> bool:
    return _normalize("nota di trasparenza") in _normalize(line)


def integrity_problems(
    lesson: str, entry: CurriculumDay, research: ResearchOutput, required_sections: list[str]
) -> list[str]:
    problems: list[str] = []
    problems.extend(quote_problems(lesson, research))
    problems.extend(attribution_problems(lesson, entry, research))
    problems.extend(section_problems(lesson, required_sections))
    words = len(lesson.split())
    if words < MIN_LESSON_WORDS:
        problems.append(
            f"lesson is too short: {words} words (minimum {MIN_LESSON_WORDS}); "
            "rewrite with the required depth"
        )
    if research.has_verified_primary and any(_transparency_note(line) for line in lesson.splitlines()):
        problems.append(
            "transparency note ('Nota di trasparenza') present even though verified "
            "primary passages exist"
        )
    return problems


# ---------------------------------------------------------------------------
# Deterministic sanitizer: even if the writer could not fix an issue, the file
# that lands on disk must not contain invented quotations or false transparency
# claims. We mechanically strip the offending quote marks / notes.
# ---------------------------------------------------------------------------

_QUOTE_PATTERNS = [
    re.compile(r'"((?:[^"\\\n]|\\.)*)"'),
    re.compile(r"«([^»]+)»"),
    re.compile(r"“([^”]+)”"),
]


def _allowed_quote(norm_span: str, titles: set[str], verified: set[str]) -> bool:
    return any(_quote_matches(norm_span, t) for t in titles) or any(
        _quote_matches(norm_span, v) for v in verified
    )


def sanitize_lesson(lesson: str, research: ResearchOutput) -> tuple[str, int]:
    """Return (cleaned lesson, number of fake-quote spans removed).

    * Any quoted span that is neither a verified primary passage nor a source
      title loses its quotation marks (its text stays as plain paraphrase).
    * A false 'Nota di trasparenza' is removed when a verified passage exists.
    """
    titles = {
        _normalize(t)
        for t in [s.title for s in research.primary_sources + research.secondary_sources]
        if t
    }
    verified = {
        _normalize(p.text)
        for p in research.primary_passages
        if p.verification_status == "verified" and p.text
    }

    removed = 0
    for pattern in _QUOTE_PATTERNS:
        new_lesson, count = _replace_quotes(lesson, pattern, titles, verified)
        lesson = new_lesson
        removed += count

    if research.has_verified_primary:
        kept = [line for line in lesson.splitlines() if not _transparency_note(line)]
        if len(kept) != len(lesson.splitlines()):
            removed += 1
        lesson = "\n".join(kept)

    return lesson, removed


def _replace_quotes(
    lesson: str, pattern: re.Pattern[str], titles: set[str], verified: set[str]
) -> tuple[str, int]:
    count = 0

    def _repl(match: re.Match) -> str:
        nonlocal count
        span = match.group(1)
        if _allowed_quote(_normalize(span), titles, verified):
            return match.group(0)
        count += 1
        return span

    return pattern.sub(_repl, lesson), count