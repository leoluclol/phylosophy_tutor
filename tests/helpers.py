"""Shared fakes for hermetic tests (no network, no real LLM)."""
from __future__ import annotations

import json
import re

from src.config import Config
from src.llm import RoleConfig
from src.models import FetchedPage, SearchResult
from src.search import SearchProvider

GUTENBERG = "https://www.gutenberg.org/files/7370/7370-h/7370-h.htm"
SEP_PROPERTY = "https://plato.stanford.edu/entries/property/"

FAKE_PASSAGE = (
    "The labour of his body and the work of his hands, we may say, are "
    "properly his. He hath mixed his labour with, and joined to something "
    "that is his own, and thereby makes it his property."
)
FAKE_PAGE_TEXT = (
    "Some introduction text about property. "
    + FAKE_PASSAGE
    + " More surrounding text so the passage is embedded in a larger body."
)


class StubSearchProvider(SearchProvider):
    name = "stub"

    def __init__(self, results: list[SearchResult] | None = None):
        self.results = results or []
        self.calls: list[str] = []

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        self.calls.append(query)
        return self.results[:max_results]


def stub_fetcher(url: str) -> FetchedPage:
    if "gutenberg" in url:
        return FetchedPage(url=url, title="Two Treatises of Government", source_status="fetched", text=FAKE_PAGE_TEXT)
    if "plato.stanford.edu" in url:
        return FetchedPage(url=url, title="Property SEP", source_status="fetched", text="Property is a bundle of rights... " + FAKE_PASSAGE)
    return FetchedPage(url=url, source_status="unavailable", reason="offline in tests")


class CountingLLM:
    """Inspectable fake LLM that answers each stage deterministically."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str, role: RoleConfig) -> str:
        self.calls.append((system, user))
        if '"queries"' in user or "query" in user.lower() and self._looks_like_query_request(user):
            return json.dumps({"queries": ["property definition sep", "locke full text"]})
        if "Materiale web raccolto" in user:
            day = self._day(user, 1)
            return json.dumps(self._research(day))
        if "Ricerca da revisionare" in user:
            return json.dumps({
                "critical_issues": [],
                "citation_issues": [],
                "missing_perspectives": [],
                "oversimplifications": [],
                "required_revisions": [],
                "approved_claims": ["ok"],
            })
        # writer stage
        day = self._day(user, 1)
        return self._lesson(day)

    @staticmethod
    def _looks_like_query_request(user: str) -> bool:
        return "queries" in user and "query di ricerca" in user

    @staticmethod
    def _day(user: str, default: int) -> int:
        match = re.search(r"giorno (\d+)", user, flags=re.IGNORECASE)
        return int(match.group(1)) if match else default

    def _research(self, day: int) -> dict:
        return {
            "lesson_day": day,
            "research_question": "What does it mean to own something?",
            "primary_sources": [
                {
                    "author": "John Locke",
                    "title": "Second Treatise of Government",
                    "location": "Ch. V",
                    "url": GUTENBERG,
                    "relevance": "Locke's labour theory of property.",
                    "verified": True,
                }
            ],
            "primary_passages": [
                {
                    "text": FAKE_PASSAGE,
                    "source": "John Locke",
                    "location": "Ch. V, §27",
                    "url": GUTENBERG,
                    "verification_status": "verified",
                }
            ],
            "secondary_sources": [
                {
                    "author": "SEP",
                    "title": "Property",
                    "url": SEP_PROPERTY,
                    "relevance": "Analytic overview.",
                }
            ],
            "key_claims": ["Locke grounds property in labour.", "The proviso limits acquisition."],
            "competing_interpretations": [
                {
                    "topic": "Meaning of the sufficiency proviso",
                    "interpretations": ["Maximalist reading", "Minimalist reading"],
                    "disagreement": "Whether the proviso constrains all future acquisition.",
                    "open_question": "Unresolved.",
                }
            ],
            "uncertainties": ["None in the fake."],
        }

    def _lesson(self, day: int) -> str:
        return f"""# Giorno {day} — Test lesson

## Domanda fondamentale

What does it mean?

## Obiettivi

- Understand ownership.

## Perché questa domanda conta

Because property talk is everywhere.

## Contesto

Context.

## Fonte primaria

> {FAKE_PASSAGE}

**Fonte:** John Locke
**Opera:** Second Treatise of Government
**Capitolo/sezione:** Ch. V
**URL:** {GUTENBERG}

## Spiegazione

Explanation.

## Argomento principale

Main argument.

## Obiezione più forte

Strongest objection.

## Possibile risposta

Reply.

## Dove rimane il problema

Open problem.

## Confronto con altre posizioni

Comparison.

## Domande per riflettere

1. Q1
2. Q2
3. Q3

## Esercizio

Write an argument.

## Fonti

### Fonti primarie

- Locke, *Second Treatise*, Ch. V.

### Fonti secondarie

- SEP, Property.

### Altre fonti

- none
"""


def make_temp_config(tmp_path, root) -> Config:
    from src.config import Config as RealConfig

    cfg = RealConfig(
        {
            "llm": {
                "provider": "openrouter",
                "researcher": {"model": "fake"},
                "critic": {"model": "fake"},
                "writer": {"model": "fake"},
            },
            "search": {"enabled": False, "max_fetch": 4, "max_page_chars": 4000},
            "curriculum": {"file": "curriculum/property.yaml", "month": "property"},
            "generation": {"output_dir": str(tmp_path / "lessons"), "cache_dir": str(tmp_path / ".cache")},
        },
        root=root,
    )
    return cfg