"""A small, manually verified registry of authoritative secondary sources.

This is a deterministic bootstrap: for any lesson, the researcher always gets
pointers to dependable academic sources (SEP, IEP, canonical archives)
regardless of whether the web-search engine cooperates. URLs here were
verified reachable during development.
"""
from __future__ import annotations

from .models import CurriculumDay, KnownSource

# author keywords -> authoritative secondary sources
AUTHOR_SOURCES: dict[tuple[str, ...], list[KnownSource]] = {
    ("locke",): [
        KnownSource(
            author="Christopher Morris (ed.)",
            title="Property, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/property/",
            note="Analisi accademica della proprietà, incluse le teorie lockeane.",
        ),
        KnownSource(
            author="Alexander Moseley",
            title="John Locke: Political Philosophy, Internet Encyclopedia of Philosophy",
            url="https://iep.utm.edu/locke-po/",
            note="Introduzione accademica a Locke.",
        ),
    ],
    ("marx", "karl marx"): [
        KnownSource(
            author="Matt Vidal, Jean-Nicolas Beaudry, Vincent Guibert",
            title="Karl Marx, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/marx/",
            note="Panoramica accademica del pensiero di Marx.",
        )
    ],
    ("nozick",): [
        KnownSource(
            author="Robert Nozick",
            title="Libertarianism, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/libertarianism/",
            note="Sezioni dedicate a Nozick.",
        ),
        KnownSource(
            author="Leif Wenar",
            title="Rights, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/rights/",
            note="Fondamenti sui diritti, incluse posizioni libertarie.",
        ),
    ],
    ("cohen", "g. a. cohen", "g.a. cohen"): [
        KnownSource(
            author="Miriam Ronzoni (ed.)",
            title="G. A. Cohen, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/cohen/",
            note="Presentazione del pensiero di Cohen, incl. self-ownership.",
        ),
        KnownSource(
            author="Nils Holtug, Kasper Lippert-Rasmussen",
            title="Equality, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/equality/",
            note="Contesto egualitario.",
        ),
    ],
    ("rawls", "john rawls"): [
        KnownSource(
            author="Leif Wenar",
            title="John Rawls, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/rawls/",
            note="Panoramica della giustizia come equità.",
        ),
        KnownSource(
            author="Ben Davies",
            title="Original Position, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/original-position/",
            note="Posizione originaria e velo d'ignoranza.",
        ),
    ],
}

# topic keywords -> authoritative secondary sources
TOPIC_SOURCES: dict[tuple[str, ...], list[KnownSource]] = {
    ("intellectual property", "copyright", "idea"): [
        KnownSource(
            author="Adam Moore, Ken Himma",
            title="Intellectual Property, Stanford Encyclopedia of Philosophy",
            url="https://plato.stanford.edu/entries/intellectual-property/",
            note="Analisi filosofica della proprietà intellettuale.",
        )
    ],
}


def secondary_sources_for(entry: CurriculumDay) -> list[KnownSource]:
    """Authoritative secondary sources to always hand the researcher."""
    found: dict[tuple[str, str], KnownSource] = {}

    for src in entry.known_secondary_sources:
        found[(src.url or src.title, src.title)] = src

    authors = [a.strip().lower() for a in entry.authors]
    for keywords, sources in AUTHOR_SOURCES.items():
        if any(any(k in author for k in keywords) for author in authors):
            for src in sources:
                found[(src.url, src.title)] = src

    topic_flags = " ".join(entry.concepts).lower() + " " + " ".join(entry.objectives).lower()
    for keywords, sources in TOPIC_SOURCES.items():
        if any(k in topic_flags for k in keywords):
            for src in sources:
                found[(src.url, src.title)] = src

    return list(found.values())