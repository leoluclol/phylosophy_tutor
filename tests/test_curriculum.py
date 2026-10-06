"""Curriculum structure tests: 30 days, required fields, ordering."""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from src.config import ROOT
from src.models import CurriculumMonth

CURRICULUM = ROOT / "curriculum" / "property.yaml"


class TestMonthProperties(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = yaml.safe_load(CURRICULUM.read_text(encoding="utf-8"))
        cls.month = CurriculumMonth.model_validate(raw["month"])

    def test_has_twenty_days(self):
        self.assertGreaterEqual(len(self.month.days), 30)

    def test_exactly_thirty_days(self):
        self.assertEqual(len(self.month.days), 30)

    def test_days_numbered_sequentially(self):
        self.assertEqual([d.day for d in self.month.days], list(range(1, 31)))

    def test_every_day_has_title_question_objectives(self):
        for day in self.month.days:
            self.assertTrue(day.title.strip(), f"day {day.day}: missing title")
            self.assertTrue(day.fundamental_question.strip(), f"day {day.day}: missing fundamental_question")
            self.assertTrue(day.objectives, f"day {day.day}: missing objectives")
            for obj in day.objectives:
                self.assertTrue(str(obj).strip(), f"day {day.day}: empty objective")
            self.assertTrue(day.why_it_matters.strip(), f"day {day.day}: missing why_it_matters")

    def test_day_descriptions_match_brief(self):
        # Day 3 must target Locke's Second Treatise ch. V.
        day3 = self.month.get_day(3)
        texts = " ".join(day3.concepts).lower() + " " + day3.title.lower()
        self.assertIn("locke", day3.authors[0].lower())
        self.assertIn("labour", texts)
        self.assertIn("appropriation", texts)

    def test_known_primary_sources_have_locations(self):
        for day in self.month.days:
            for src in day.known_primary_sources:
                self.assertTrue(src.title.strip(), f"day {day.day}: primary source without title")
                self.assertTrue(src.author.strip(), f"day {day.day}: primary source without author")


if __name__ == "__main__":
    unittest.main()