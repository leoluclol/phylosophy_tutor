"""Tests for the mechanical integrity checks (src/checks.py)."""
from __future__ import annotations

import unittest

from src.checks import (
    REQUIRED_SECTIONS,
    attribution_problems,
    integrity_problems,
    quote_problems,
    sanitize_lesson,
    section_problems,
)
from src.config import ROOT
from src.models import CurriculumMonth, ResearchOutput

import yaml

VERIFIED_TEXT = "The labour of his body, and the work of his hands, we may say, are properly his."


def _research(verified: list[dict] | None = None) -> ResearchOutput:
    return ResearchOutput(
        lesson_day=1,
        research_question="q",
        primary_passages=verified or [
            {"text": VERIFIED_TEXT, "source": "Locke", "verification_status": "verified"}
        ],
        primary_sources=[
            {"author": "John Locke", "title": "Second Treatise", "url": "https://x.example", "verified": True}
        ],
        secondary_sources=[{"author": "SEP", "title": "Property", "url": "https://y.example"}],
    )


def _entry():
    raw = yaml.safe_load((ROOT / "curriculum" / "property.yaml").read_text(encoding="utf-8"))
    return CurriculumMonth.model_validate(raw["month"]).get_day(1)


class TestQuoteProblems(unittest.TestCase):
    def test_verbatim_quote_passes(self):
        lesson = f'## Fonte primaria\n> "{VERIFIED_TEXT}"\n\n## Argomento principale\nok'
        self.assertEqual(quote_problems(lesson, _research()), [])

    def test_invented_quote_is_flagged(self):
        lesson = '> "Harold owns the moon because he said so."'
        problems = quote_problems(lesson, _research())
        self.assertEqual(len(problems), 1)
        self.assertIn("not matching any verified", problems[0])

    def test_translated_quote_is_flagged(self):
        lesson = 'Secondo Locke, "la proprietà deriva unicamente dal lavoro individuale".'
        problems = quote_problems(lesson, _research())
        self.assertEqual(len(problems), 1)

    def test_no_verified_passage_means_no_quote_allowed(self):
        research = _research(verified=[])
        lesson = 'La lezione contiene una citazione "provata".'
        self.assertEqual(quote_problems(lesson, research), [])
        lesson2 = 'Una citazione "inventata e senza alcuna fonte primaria a sostegno del contenuto".'
        self.assertEqual(len(quote_problems(lesson2, research)), 1)


class TestAttributionProblems(unittest.TestCase):
    def test_supported_author_passes(self):
        lesson = "Locke sostiene che il lavoro crea proprietà."
        self.assertEqual(attribution_problems(lesson, _entry(), _research()), [])

    def test_unsupported_authors_flagged(self):
        lesson = "Secondo Rousseau e Hegel la proprietà è ingiusta."
        problems = attribution_problems(lesson, _entry(), _research())
        self.assertEqual(len(problems), 2)

    def test_plato_url_is_not_a_name_drop(self):
        lesson = "Fonti: https://plato.stanford.edu/entries/property/"
        self.assertEqual(attribution_problems(lesson, _entry(), _research()), [])


class TestIntegrityProblems(unittest.TestCase):
    LONG_TEXT = ("profondità ".replace(" ", " profondità ") * 85).strip()  # ~2.5k+ words

    def test_clean_lesson_passes(self):
        lesson = "\n".join([f"{s}\n{self.LONG_TEXT}" for s in REQUIRED_SECTIONS])
        lesson += ' > "The labour of his body, and the work of his hands, we may say, are properly his."'
        self.assertEqual(integrity_problems(lesson, _entry(), _research(), REQUIRED_SECTIONS), [])

    def test_short_lesson_is_rejected(self):
        lesson = "\n".join([f"{s}\nx" for s in REQUIRED_SECTIONS])
        problems = integrity_problems(lesson, _entry(), _research(), REQUIRED_SECTIONS)
        self.assertTrue(any("too short" in p for p in problems))

    def test_missing_section_reported(self):
        self.assertEqual(section_problems("no sections here", ["## Fonti"]), ['missing section \'## Fonti\''])

    def test_false_transparency_note_reported(self):
        lesson = self.LONG_TEXT + "\nNota di trasparenza: non è stato possibile verificare una fonte primaria."
        problems = integrity_problems(lesson, _entry(), _research(), REQUIRED_SECTIONS)
        self.assertTrue(any("transparency" in p for p in problems))


class TestSanitize(unittest.TestCase):
    def test_sanitizer_strips_invented_quotes(self):
        research = _research()
        lesson = ('X "la proprietà è un diritto naturale derivante dal lavoro" Y '
                  '"%s"' % VERIFIED_TEXT)
        cleaned, count = sanitize_lesson(lesson, research)
        self.assertEqual(count, 1)
        self.assertNotIn('"la proprietà è un diritto naturale derivante dal lavoro"', cleaned)
        self.assertIn('"The labour of his body, and the work of his hands, we may say, are properly his."', cleaned)

    def test_sanitizer_keeps_source_titles(self):
        research = _research()
        lesson = 'Fonti:\n- Lawrence Becker, "Property", URL: https://plato.stanford.edu/entries/property/'
        cleaned, count = sanitize_lesson(lesson, research)
        self.assertEqual(count, 0)
        self.assertIn('"Property"', cleaned)


if __name__ == "__main__":
    unittest.main()