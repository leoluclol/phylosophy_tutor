"""Researcher phase: gather and verify material for one lesson.

The researcher never explains: it collects pages, extracts the relevant
pieces, and reports them in a structured, validated JSON (research.json).

The researcher is deliberately split into two LLM calls so that web search
(query formulation) and reasoning (analysis) are separated:
   1. formulate queries
   2. run searches + fetch pages  (deterministic Python, NOT the LLM)
   3. analyze gathered material -> research.json (LLM)
   4. mechanically re-verify every "verified" passage
"""
from __future__ import annotations

import json
import logging
import re
import string
import unicodedata
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from .config import Config
from .llm import LLMClient, chat_json
from .models import CurriculumDay, FetchedPage, ResearchOutput
from .search import SearchProvider
from .sources import secondary_sources_for

logger = logging.getLogger("phylosophy")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class _QueryList(BaseModel):
    queries: list[str] = Field(default_factory=list)


def _load_prompt(name: str) -> string.Template:
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    return string.Template(text)


def formulate_queries(
    entry: CurriculumDay,
    client: LLMClient,
    config: Config,
) -> list[str]:
    """Ask the model for search queries; fall back to the curriculum's."""
    template = _load_prompt("researcher_queries.txt")
    prompt = template.substitute(entry_json=json.dumps(entry.model_dump(), ensure_ascii=False, indent=2))
    result = None
    try:
        result = chat_json(
            client,
            system="You produce search-engine queries only. Reply with one valid JSON object.",
            user=prompt,
            role=config.researcher,
            validator=lambda d: _QueryList.model_validate(d),
            normalize=lambda d: d,
            max_retries=config.max_json_retries,
        )
    except Exception as exc:  # noqa: BLE001 - fallback must survive any failure
        logger.warning("Query formulation failed (%s); using curriculum queries.", exc)

    queries = [q for q in result.queries if q.strip()] if result and result.queries else []
    if queries:
        return queries[:6]
    logger.info("No model-generated queries; using suggested_queries from the curriculum.")
    return entry.suggested_queries or []


def _norm_url(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return (parsed.netloc + parsed.path).lower().rstrip("/")


def gather_material(
    entry: CurriculumDay,
    queries: list[str],
    config: Config,
    provider: Optional[SearchProvider],
    fetcher: Callable[[str], FetchedPage],
) -> list[FetchedPage]:
    """Combine deterministic known sources with live search results.

    * Known primary/secondary URLs from the curriculum and the source registry
      are always attempted first (they are the priority sources, §6 of brief).
    * Search results fill the gaps. Duplicates are removed, pages are fetched
      through the cached fetcher; failures are recorded, never faked.
    """
    candidates: list[tuple[float, str, str]] = []  # (priority, url, kind)

    def add(cand_url: str, priority: float, kind: str) -> None:
        candidates.append((priority, cand_url, kind))

    for src in entry.known_primary_sources:
        if src.url:
            add(src.url, -60, "known-primary")
    for src in entry.known_secondary_sources:
        if src.url:
            add(src.url, -50, "known-secondary")
    for src in secondary_sources_for(entry):
        if src.url:
            add(src.url, -45, "registry-secondary")

    if provider is not None:
        for query in queries:
            try:
                results = provider.search(query, max_results=config.search_max_results)
            except Exception as exc:  # noqa: BLE001 - search must not break the pipeline
                logger.warning("search failed for %r: %s", query, exc)
                results = []
            for rank, hit in enumerate(results):
                if not hit.url:
                    continue
                # Prefer focused academic results slightly over generic web hits.
                add(hit.url, 5 + rank - hit.priority / 10.0, "search")

    # Deduplicate, keep lowest priority per URL.
    best: dict[str, tuple[float, str]] = {}
    for priority, url, kind in candidates:
        key = _norm_url(url)
        if key not in best or priority < best[key][0]:
            best[key] = (priority, url)

    ordered = sorted(best.values(), key=lambda t: t[0])[: config.search_max_fetch]
    if not ordered:
        logger.warning("No candidate sources at all for day %d.", entry.day)
        return []

    pages: list[FetchedPage] = []
    for priority, url in ordered:
        logger.debug("fetching [%.0f] %s", priority, url)
        try:
            pages.append(fetcher(url))
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch threw for %s: %s", url, exc)
            pages.append(FetchedPage(url=url, source_status="unavailable", reason=str(exc)[:200]))

    ok = sum(1 for p in pages if p.source_status == "fetched")
    logger.info("Materiale raccolto: %d/%d pagine recuperate.", ok, len(pages))
    return pages


_HEADER_MARKER = re.compile(r"(chapter|chap\.?\s*[ivxIVX]|sect\.?|section|by permission|^\s*$)", re.IGNORECASE | re.MULTILINE)


def _locator_fragments(hint: str) -> list[str]:
    """Yield candidate anchor fragments from a free-form location string.

    For "Book II, Ch. V 'Of Property' (§§ 25–51)" this yields the section
    reference first ("Sect. 25"), then the quoted title, then other tokens.
    """
    candidates: list[str] = []
    # 1) Explicit section references: "Sect. 25", "§§ 25–51", "§27".
    for match in re.findall(r"(?:Sect\.?\s*|§s?\.?\s*)(\d+)", hint):
        candidates.append(f"Sect. {match}")
    for match in re.findall(r"§s?\s*\.?\s*(\d+)–?(\d*)", hint):
        candidates.append(f"Sect. {match[0]}")
    # 2) Quoted section titles.
    for match in re.findall(r"'([^']{4,90})'", hint):
        candidates.append(match)
    # 3) Chapter tokens.
    for match in re.findall(r"(?:Chapter|CHAPTER|Ch\.|CHAP\.)\s*(?:\.?\s*)?[IVX]+", hint):
        candidates.append(match)
    # 4) Long alphanumeric runs as a fallback.
    for match in re.findall(r"[\wÀ-ÿ '-]{10,90}", hint):
        run = match.strip()
        if run and run not in candidates and any(c.isalpha() for c in run) and "§" not in run:
            candidates.append(run)
    return candidates


def _is_header_match(text: str, idx: int) -> bool:
    """A match is 'header-like' if it starts a line or follows a chapter marker,
    which keeps us from anchoring on ordinary phrases like 'preserving of
    property' inside a sentence."""
    line_start = text.rfind("\n", 0, idx)
    if line_start == -1 or idx - line_start <= 4:
        return True
    before = text[max(0, idx - 90) : idx]
    return bool(_HEADER_MARKER.search(before))


def _slice_excerpt(text: str, hints: list[str], max_chars: int) -> str:
    """Return a snippet of ``text`` anchored on the best located hint.

    Falls back to the head of the page when no hint can be located, so that
    secondary sources (SEP etc.) are sliced from the start.
    """
    if not hints or not text:
        return text[:max_chars]

    anchor: int | None = None
    for hint in hints:
        for frag in _locator_fragments(hint):
            if not frag:
                continue
            frag_upper = frag.upper()
            matches: list[int] = []
            start = 0
            while True:
                idx = text.upper().find(frag_upper, start)
                if idx == -1:
                    break
                matches.append(idx)
                start = idx + len(frag_upper)
            header_matches = [idx for idx in matches if _is_header_match(text, idx)]
            if header_matches:
                # Prefer the last header-like occurrence (real bodies usually
                # come after tables of contents / catchword pages).
                anchor = header_matches[-1]
                break
        if anchor is not None:
            break

    if anchor is None:
        return text[:max_chars]
    start = max(0, anchor - 600)
    return text[start : start + max_chars]


def _format_material(
    pages: list[FetchedPage],
    config: Config,
    hints_by_url: Optional[dict[str, list[str]]] = None,
) -> str:
    hints_by_url = hints_by_url or {}
    budget = config.search_max_material_chars
    blocks: list[str] = []
    used = 0
    for i, page in enumerate(pages, start=1):
        if page.source_status == "unavailable":
            blocks.append(
                f"[PAGE {i}] URL: {page.url}\nSTATUS: UNAVAILABLE ({page.reason or 'reason unknown'})"
            )
            continue
        if page.source_status == "empty":
            blocks.append(f"[PAGE {i}] URL: {page.url}\nSTATUS: EMPTY (no text extracted)")
            continue

        excerpt = _slice_excerpt(page.text, hints_by_url.get(page.url, []), config.search_max_page_chars)
        if not excerpt:
            excerpt = page.text[: config.search_max_page_chars]
        block = f"[PAGE {i}] URL: {page.url}\nTITLE: {page.title or '(no title)'}\nTEXT:\n{excerpt}"
        size = len(block)
        if used and used + size > budget:
            continue  # already beyond budget; drop lower-priority pages
        blocks.append(block)
        used += size
    return "\n\n" + "\n\n".join(blocks) + "\n"


def analyze(
    entry: CurriculumDay,
    pages: list[FetchedPage],
    client: LLMClient,
    config: Config,
) -> ResearchOutput:
    template = _load_prompt("researcher.txt")
    prompt = template.substitute(
        day=entry.day,
        entry_json=json.dumps(entry.model_dump(), ensure_ascii=False, indent=2),
        material=_format_material(pages, config, hints_by_url=_primary_hints(entry)),
    )
    return chat_json(
        client,
        system=(
            "You are the research stage of a philosophy tutor. You never write "
            "prose: you emit one valid JSON object with primary sources, "
            "verbatim-verified passages, secondary sources and uncertainties."
        ),
        user=prompt,
        role=config.researcher,
        validator=lambda d: ResearchOutput.model_validate(d),
        normalize=lambda d: _normalize_research_shapes(d),
        max_retries=config.max_json_retries,
    )


def _normalize_research_shapes(obj: dict) -> dict:
    from .models import normalize_research

    return normalize_research(obj)


def _primary_hints(entry: CurriculumDay) -> dict[str, list[str]]:
    """Map primary-source URLs to location hints used to anchor excerpts."""
    hints: dict[str, list[str]] = {}
    for src in entry.known_primary_sources:
        if src.url and src.location:
            hints.setdefault(src.url, []).append(src.location)
    return hints


def verify_passages(research: ResearchOutput, pages: list[FetchedPage]) -> ResearchOutput:
    """Mechanically downgrade any 'verified' passage not found in fetched text.

    This is the anti-fabrication backstop: no LLM assertion of "verified"
    survives unless the exact text appears in a page we actually read.
    """
    corpora = [p.text for p in pages if p.source_status == "fetched" and p.text]
    if not corpora:
        return research

    def norm(text: str) -> str:
        text = unicodedata.normalize("NFKC", text)
        return re.sub(r"[^0-9a-zA-Z\u00c0-\u024f]", "", text.lower())

    for passage in research.primary_passages:
        if passage.verification_status != "verified":
            continue
        needle = norm(passage.text)
        if not needle:
            passage.verification_status = "unverified"
            continue
        found = any(needle in norm(corpus) for corpus in corpora)
        if not found:
            logger.warning(
                "Downgraded passage to 'unverified' (not found verbatim in fetched pages): "
                "%r (source: %s).",
                passage.text[:80],
                passage.source,
            )
            passage.verification_status = "unverified"
    return research


def run(
    entry: CurriculumDay,
    client: LLMClient,
    config: Config,
    provider: Optional[SearchProvider] = None,
    fetcher: Optional[Callable[[str], FetchedPage]] = None,
) -> ResearchOutput:
    """Full researcher pipeline for one curriculum day."""
    from .fetch import make_fetcher

    queries = formulate_queries(entry, client, config)
    logger.debug("queries: %s", queries)
    fetcher = fetcher or make_fetcher(
        cache_dir=config.cache_dir,
        max_chars=200000,
        timeout=config.search_fetch_timeout,
    )
    pages = gather_material(entry, queries, config, provider, fetcher)
    research = analyze(entry, pages, client, config)
    research = verify_passages(research, pages)
    return research