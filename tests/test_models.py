"""Pydantic schema tests for research and critique JSON."""
from __future__ import annotations

import unittest

import yaml
from pydantic import ValidationError

from src.models import (
    CritiqueOutput,
    CurriculumMonth,
    ResearchOutput,
    normalize_critique,
    normalize_research,
)
from src.config import ROOT


class TestResearchSchema(unittest.TestCase):
    def test_valid_research_validates(self):
        data = {
            "lesson_day": 1,
            "research_question": "q",
            "primary_sources": [
                {"author": "J.L.", "title": "Second Treatise", "url": "https://example.com", "verified": True}
            ],
            "primary_passages": [
                {"text": "a quote", "source": "J.L.", "verification_status": "unverified"}
            ],
            "secondary_sources": [],
            "key_claims": ["ok"],
            "competing_interpretations": [],
            "uncertainties": [],
        }
        model = ResearchOutput.model_validate(data)
        self.assertEqual(model.lesson_day, 1)
        self.assertFalse(model.has_verified_primary)

    def test_verified_passage_counts(self):
        data = {
            "lesson_day": 2,
            "research_question": "q",
            "primary_passages": [{"text": "x", "verification_status": "verified"}],
        }
        model = ResearchOutput.model_validate(data)
        self.assertTrue(model.has_verified_primary)

    def test_invalid_status_rejected(self):
        data = {
            "lesson_day": 1,
            "primary_passages": [{"text": "x", "verification_status": "maybe"}],
        }
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate(data)

    def test_missing_lesson_day_rejected(self):
        with self.assertRaises(ValidationError):
            ResearchOutput.model_validate({"research_question": "q"})

    def test_normalize_fixes_shape_mistakes(self):
        raw = {
            "lesson_day": 1,
            "key_claims": "single claim not a list",  # LLM mistakes happen
            "competing_interpretations": "Rawls vs Nozick",
        }
        normalized = normalize_research(raw)
        model = ResearchOutput.model_validate(normalized)
        self.assertEqual(model.key_claims, ["single claim not a list"])
        self.assertEqual(model.competing_interpretations[0].topic, "Rawls vs Nozick")


class TestCritiqueSchema(unittest.TestCase):
    def test_valid_critique_validates(self):
        data = {
            "critical_issues": [{"issue": "strawman risk", "severity": "major", "suggestion": "rewrite"}],
            "citation_issues": [],
            "missing_perspectives": ["Marx"],
            "oversimplifications": [],
            "required_revisions": [],
            "approved_claims": ["ok"],
        }
        model = CritiqueOutput.model_validate(data)
        self.assertEqual(model.critical_issues[0].severity, "major")

    def test_normalize_wraps_strings(self):
        raw = {"critical_issues": "some vague issue", "missing_perspectives": None}
        model = CritiqueOutput.model_validate(normalize_critique(raw))
        self.assertEqual(model.critical_issues[0].issue, "some vague issue")
        self.assertEqual(model.missing_perspectives, [])

    def test_unknown_severity_rejected(self):
        with self.assertRaises(ValidationError):
            CritiqueOutput.model_validate({"critical_issues": [{"issue": "x", "severity": "fatal"}]})


class TestCurriculumSchema(unittest.TestCase):
    def test_wrapped_month_ignores_extra_top_keys(self):
        raw = yaml.safe_load((ROOT / "curriculum" / "property.yaml").read_text(encoding="utf-8"))
        month = CurriculumMonth.model_validate(raw["month"])
        self.assertEqual(len(month.days), 30)


if __name__ == "__main__":
    unittest.main()