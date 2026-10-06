"""Writer phase: produce the final Markdown lesson.

The writer is constrained to the verified material in research.json and is
explicitly told which revisions the critic demands. It emits Markdown only.
"""
from __future__ import annotations

import json
import logging
import string
from pathlib import Path

from . import checks
from .config import Config
from .llm import LLMClient
from .models import CurriculumDay, CritiqueOutput, ResearchOutput

logger = logging.getLogger("phylosophy")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def run(
    entry: CurriculumDay,
    research: ResearchOutput,
    critique: CritiqueOutput,
    client: LLMClient,
    config: Config,
    integrity_issues: list[str] | None = None,
) -> str:
    integrity_issues = integrity_issues or []
    if integrity_issues:
        integrity_block = "IL CONTROLLO MECCANICO DI INTEGRITÀ È FALLITO. CORREGGI ORA TUTTI i problemi elencati e rigenera l'intera lezione:\n" + "\n".join(
            f"- {issue}" for issue in integrity_issues
        )
    else:
        integrity_block = "Nessun problema di integrità segnalato: procedi."

    template = string.Template((PROMPTS_DIR / "writer.txt").read_text(encoding="utf-8"))
    prompt = template.substitute(
        day=entry.day,
        entry_json=json.dumps(entry.model_dump(), ensure_ascii=False, indent=2),
        research_json=json.dumps(research.model_dump(), ensure_ascii=False, indent=2),
        critique_json=json.dumps(critique.model_dump(), ensure_ascii=False, indent=2),
        integrity_issues=integrity_block,
        allowed_authors=", ".join(_allowed_authors(entry, research)),
    )
    lesson = client.complete(
        system=(
            "You are the final writer of a philosophy lesson. You output ONLY "
            "the Markdown of the lesson, in Italian, no surrounding text, no "
            "code fences. The lesson must be LONG and deep: at least 2600 "
            "words. If your draft is a summary, expand it before replying."
        ),
        user=prompt,
        role=config.writer,
    )
    return _post_process(lesson, entry)


def _allowed_authors(entry: CurriculumDay, research: ResearchOutput) -> list[str]:
    names: list[str] = []
    for src in entry.known_primary_sources + entry.known_secondary_sources:
        if src.author and src.author not in names:
            names.append(src.author)
    for src in research.primary_sources + research.secondary_sources:
        if src.author and src.author not in names:
            names.append(src.author)
    for author in entry.authors:
        if author and author not in names:
            names.append(author)
    return names


def _post_process(lesson: str, entry: CurriculumDay) -> str:
    lesson = lesson.strip()
    if not lesson:
        logger.error("Writer returned an empty lesson.")
        return lesson
    lesson = _strip_markdown_fence(lesson)
    if not lesson.startswith("#"):
        # If the model added a preamble, keep the content but flag it.
        logger.warning("Lesson does not start with '# Giorno %d …'; inspect output.", entry.day)
    missing = [sec for sec in checks.REQUIRED_SECTIONS if sec not in lesson]
    if missing:
        logger.warning("Lesson is missing expected sections: %s", ", ".join(missing))
    return lesson.strip() + "\n"


def _strip_markdown_fence(text: str) -> str:
    """Remove a ``` or ```markdown fence that the model sometimes wraps around
    the whole document, which would otherwise land literally in the lesson."""
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)