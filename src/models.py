"""Pydantic models for the whole pipeline.

Every structured artifact that crosses an LLM boundary (research.json,
critique.json) is validated here. The curriculum YAML is also validated here.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Curriculum
# ---------------------------------------------------------------------------


class KnownSource(BaseModel):
    """A source the researcher should already know about (primary or secondary)."""

    author: str = ""
    title: str = ""
    location: str = ""
    url: str = Field(default="", description="Empty when the text is copyright-protected and no legal URL is known.")
    note: str = ""


class CurriculumDay(BaseModel):
    """One entry of the curriculum."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    day: int = Field(ge=1)
    title: str = Field(min_length=1)
    fundamental_question: str = Field(min_length=1)
    why_it_matters: str = ""
    objectives: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    known_primary_sources: list[KnownSource] = Field(default_factory=list)
    known_secondary_sources: list[KnownSource] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    requirements: str = ""

    @field_validator("objectives")
    @classmethod
    def _objectives_nonempty(cls, v: list[str]) -> list[str]:
        clean = [o for o in v if o.strip()]
        if not clean:
            raise ValueError("every day must define at least one objective")
        return clean


class CurriculumMonth(BaseModel):
    """The full month curriculum."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str
    title: str
    theme: str = ""
    first_day_question: str = ""
    days: list[CurriculumDay] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_days(self) -> "CurriculumMonth":
        numbers = [d.day for d in self.days]
        expected = list(range(1, len(self.days) + 1))
        if numbers != expected:
            raise ValueError(f"days must be numbered sequentially 1..{len(self.days)}, got {numbers}")
        return self

    def get_day(self, day: int) -> Optional[CurriculumDay]:
        for d in self.days:
            if d.day == day:
                return d
        return None


# ---------------------------------------------------------------------------
# Research output (researcher phase)
# ---------------------------------------------------------------------------


class SourceRef(BaseModel):
    """A primary or secondary source referenced by the researcher."""

    author: str = ""
    title: str = ""
    location: str = ""
    url: str = ""
    relevance: str = ""
    verified: bool = False


class PrimaryPassage(BaseModel):
    """A short quote from a primary source.

    ``verification_status == "verified"`` is a hard promise: the exact text
    was found verbatim in a page fetched during research. The pipeline
    enforces this with a fuzzy containment check.
    """

    text: str = Field(min_length=1)
    source: str = ""
    location: str = ""
    url: str = ""
    verification_status: Literal["verified", "unverified"] = "unverified"


class InterpretationEntry(BaseModel):
    """A disputed point and the competing readings."""

    topic: str = ""
    interpretations: list[str] = Field(default_factory=list)
    disagreement: str = ""
    open_question: str = ""


class ResearchOutput(BaseModel):
    """Structured output of the researcher phase (research.json)."""

    lesson_day: int
    research_question: str = ""
    primary_sources: list[SourceRef] = Field(default_factory=list)
    primary_passages: list[PrimaryPassage] = Field(default_factory=list)
    secondary_sources: list[SourceRef] = Field(default_factory=list)
    key_claims: list[str] = Field(default_factory=list)
    competing_interpretations: list[InterpretationEntry] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @property
    def has_verified_primary(self) -> bool:
        return any(p.verification_status == "verified" for p in self.primary_passages)


# ---------------------------------------------------------------------------
# Critique output (critic phase)
# ---------------------------------------------------------------------------


class CritiqueIssue(BaseModel):
    """One critical finding of the critic phase."""

    issue: str
    severity: Literal["minor", "major", "critical"] = "major"
    suggestion: str = ""


class CitationIssue(BaseModel):
    """A citation-verification problem found by the critic."""

    source: str = ""
    issue: str = ""
    severity: Literal["minor", "major", "critical"] = "major"


class CritiqueOutput(BaseModel):
    """Structured output of the critic phase (critique.json)."""

    critical_issues: list[CritiqueIssue] = Field(default_factory=list)
    citation_issues: list[CitationIssue] = Field(default_factory=list)
    missing_perspectives: list[str] = Field(default_factory=list)
    oversimplifications: list[str] = Field(default_factory=list)
    required_revisions: list[str] = Field(default_factory=list)
    approved_claims: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Search / fetch artifacts
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """One search-engine hit."""

    title: str = ""
    url: str = ""
    snippet: str = ""
    provider: str = ""
    priority: float = Field(default=0.0, description="Lower is better; domain quality score.")


class FetchedPage(BaseModel):
    """The outcome of trying to read one page."""

    url: str
    title: str = ""
    source_status: Literal["fetched", "unavailable", "empty"] = "unavailable"
    text: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Normalization helpers: LLMs occasionally return a string where a list is
# expected (e.g. "text" instead of ["text"]). These helpers make validation
# tolerant without weakening the final schema.
# ---------------------------------------------------------------------------


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def normalize_research(obj: dict) -> dict:
    """Coerce common LLM mistakes into the ResearchOutput shape."""
    obj = dict(obj or {})
    for field in ("key_claims", "uncertainties"):
        obj[field] = _as_list(obj.get(field))
    obj.setdefault("primary_sources", [])
    obj.setdefault("primary_passages", [])
    obj.setdefault("secondary_sources", [])
    obj["competing_interpretations"] = _normalize_interpretations(
        _as_list(obj.get("competing_interpretations"))
    )
    return obj


def _normalize_interpretations(items: list) -> list[dict]:
    out = []
    for it in items:
        if isinstance(it, str):
            out.append({"topic": it, "interpretations": []})
            continue
        if not isinstance(it, dict):
            continue
        clean = dict(it)
        if isinstance(clean.get("interpretations"), str):
            clean["interpretations"] = [clean["interpretations"]]
        clean.setdefault("interpretations", [])
        out.append(clean)
    return out


def normalize_critique(obj: dict) -> dict:
    obj = dict(obj or {})
    for field in ("missing_perspectives", "oversimplifications", "required_revisions", "approved_claims"):
        obj[field] = _as_list(obj.get(field))
    for field in ("critical_issues", "citation_issues"):
        items = _as_list(obj.get(field))
        cleaned = []
        for it in items:
            if isinstance(it, str):
                cleaned.append({"issue": it or "unspecified issue"})
            elif isinstance(it, dict):
                clean = dict(it)
                if not clean.get("issue"):
                    clean["issue"] = "unspecified issue"
                cleaned.append(clean)
        obj[field] = cleaned
    return obj