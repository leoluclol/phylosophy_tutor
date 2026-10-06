"""Critic phase: an extremely skeptical review of the research output."""
from __future__ import annotations

import json
import logging
import string
from pathlib import Path

from .config import Config
from .llm import LLMClient, chat_json
from .models import CurriculumDay, CritiqueOutput, ResearchOutput

logger = logging.getLogger("phylosophy")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def run(
    entry: CurriculumDay,
    research: ResearchOutput,
    client: LLMClient,
    config: Config,
) -> CritiqueOutput:
    template = (PROMPTS_DIR / "critic.txt").read_text(encoding="utf-8")
    template = string.Template(template)
    prompt = template.substitute(
        day=entry.day,
        entry_json=json.dumps(entry.model_dump(), ensure_ascii=False, indent=2),
        research_json=json.dumps(research.model_dump(), ensure_ascii=False, indent=2),
    )
    return chat_json(
        client,
        system=(
            "You are an extremely skeptical academic reviewer of a research "
            "report for a philosophy lesson. You find flaws. Reply with one "
            "valid JSON object only."
        ),
        user=prompt,
        role=config.critic,
        validator=lambda d: CritiqueOutput.model_validate(d),
        normalize=lambda d: _normalize_critique_shapes(d),
        max_retries=config.max_json_retries,
    )


def _normalize_critique_shapes(obj: dict) -> dict:
    from .models import normalize_critique

    return normalize_critique(obj)