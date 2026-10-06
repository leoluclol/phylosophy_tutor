"""Pipeline orchestration: curriculum -> research -> critique -> lesson.

The pipeline is a plain sequence of function calls (no agents, no framework).
Caching: research.json and critique.json are cached per day under .cache/ and
reused unless --force. Search results and page fetches are cached too.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

import yaml

from .config import Config
from .llm import LLMClient, OpenRouterClient
from .models import (
    CritiqueOutput,
    CurriculumDay,
    CurriculumMonth,
    FetchedPage,
    ResearchOutput,
)
from . import checks
from . import critic as critic_mod
from . import researcher as researcher_mod
from . import writer as writer_mod

logger = logging.getLogger("phylosophy")


class PipelineError(Exception):
    """A readable, non-silent failure of the lesson-generation pipeline."""


class SkipLesson(Exception):
    """Raised when the target lesson already exists and --force was not set."""


class Pipeline:
    def __init__(
        self,
        config: Config,
        client: Optional[LLMClient] = None,
        provider=None,
        fetcher: Optional[Callable[[str], FetchedPage]] = None,
    ):
        self.config = config
        self.client = client or OpenRouterClient(config)
        if provider is None:
            from .search import build_search_provider

            provider = build_search_provider(config)
        self.provider = provider
        self.fetcher = fetcher

    # ---------------------------------------------------------------- helpers

    def _load_month(self) -> CurriculumMonth:
        path = self.config.curriculum_path
        if not path.exists():
            raise PipelineError(f"curriculum file not found: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict) and "month" in raw:
            raw = raw["month"]
        return CurriculumMonth.model_validate(raw)

    def _entry(self, month: CurriculumMonth, day: int) -> CurriculumDay:
        entry = month.get_day(day)
        if entry is None:
            raise PipelineError(
                f"day {day} not found in curriculum {self.config.month!r} "
                f"(valid days: {[d.day for d in month.days]})"
            )
        return entry

    def _cache_path(self, kind: str, day: int) -> Path:
        path = self.config.cache_dir / kind / f"day_{day:03d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------- main entry

    def run(self, day: int, force: bool = False) -> Path:
        output_dir = self.config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{day:03d}.md"

        step(1, "Loading curriculum...")
        month = self._load_month()
        entry = self._entry(month, day)

        if output_path.exists() and not force:
            logger.info("%s already exists — skipping (use --force to regenerate)", output_path)
            raise SkipLesson()

        # ------------------------------ research ----------------------------
        research_cache = self._cache_path("research", day)
        if research_cache.exists() and not force:
            step(2, "Searching sources... (cached)")
            step(3, "Running researcher... (cached)")
            research = self._load_research_cached(research_cache)
        else:
            step(2, "Searching sources...")
            step(3, "Running researcher...")
            research = researcher_mod.run(
                entry,
                client=self.client,
                config=self.config,
                provider=self.provider,
                fetcher=self.fetcher,
            )
            research_cache.write_text(
                research.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )

        self._check_primary_source(research, day)

        # ------------------------------ critique ----------------------------
        critique_cache = self._cache_path("critique", day)
        if critique_cache.exists() and not force:
            step(4, "Running critic... (cached)")
            critique = CritiqueOutput.model_validate(json.loads(critique_cache.read_text("utf-8")))
        else:
            step(4, "Running critic...")
            critique = critic_mod.run(entry, research, client=self.client, config=self.config)
            critique_cache.write_text(
                critique.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )

        # ------------------------------- writer -----------------------------
        step(5, "Running writer...")
        lesson = self._write_lesson_with_integrity(entry, research, critique)

        self._check_lesson_urls(lesson, research)
        lesson, removed_quotes = checks.sanitize_lesson(lesson, research)
        if removed_quotes:
            logger.warning(
                "Removed %d unverifiable quotation(s)/misleading note(s) from the lesson "
                "before saving.",
                removed_quotes,
            )
        output_path.write_text(lesson, encoding="utf-8")
        logger.info("Saved %s", output_path)
        return output_path

    def _write_lesson_with_integrity(
        self,
        entry: CurriculumDay,
        research: ResearchOutput,
        critique: CritiqueOutput,
    ) -> str:
        """Run the writer, re-running it when mechanical integrity checks fail.

        The writer is given up to ``max_json_retries + 1`` attempts; every
        failed attempt's list of problems is fed back into the prompt. Whatever
        remains after the last attempt is reported loudly but still saved (the
        failure must never be silent).
        """
        attempts = max(1, self.config.max_writer_attempts)
        problems: list[str] = []
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                logger.warning(
                    "Writer integrity check failed (attempt %d/%d); regenerating.",
                    attempt - 1,
                    attempts,
                )
            lesson = writer_mod.run(
                entry,
                research,
                critique,
                client=self.client,
                config=self.config,
                integrity_issues=problems,
            )
            if not lesson.strip():
                raise PipelineError("Writer produced an empty lesson; refusing to save.")
            problems = checks.integrity_problems(lesson, entry, research, checks.REQUIRED_SECTIONS)
            if not problems:
                return lesson
        for problem in problems:
            logger.warning("Integrity issue (not fixed after retries): %s", problem)
        return lesson

    # -------------------------------------------------------------- warnings

    def _check_primary_source(self, research: ResearchOutput, day: int) -> None:
        verified = [p for p in research.primary_passages if p.verification_status == "verified"]
        if not verified:
            logger.warning("WARNING: no verified primary source found for day %d", day)
        if not research.primary_passages:
            logger.warning(
                "WARNING: researcher produced no primary passages at all for day %d; "
                "the writer has been told to state this openly and must not fake a quote.",
                day,
            )

    def _check_lesson_urls(self, lesson: str, research: ResearchOutput) -> None:
        """Warn when the lesson cites URLs not present in the research output."""
        allowed = set()
        for src in research.primary_sources + research.secondary_sources:
            if src.url:
                allowed.add(_url_fingerprint(src.url))
        for passage in research.primary_passages:
            if passage.url:
                allowed.add(_url_fingerprint(passage.url))

        found = set(re.findall(r"https?://[^\s)\]>'\"`]+", lesson))
        foreign = [u for u in found if u and _url_fingerprint(u) not in allowed]
        if foreign:
            logger.warning(
                "Lesson mentions URLs not present in research.json (possible fabrication): %s",
                ", ".join(sorted(foreign)[:5]),
            )

    def _load_research_cached(self, path: Path) -> ResearchOutput:
        return ResearchOutput.model_validate(json.loads(path.read_text(encoding="utf-8")))



def _url_fingerprint(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return (parsed.netloc.lower().removeprefix("www.") + parsed.path).rstrip("/").lower()


def step(number: int, message: str) -> None:
    logger.info("[%d/5] %s", number, message)