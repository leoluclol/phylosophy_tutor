"""End-to-end (hermetic) pipeline tests with fakes in place of network/LLM."""
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path

from src.config import ROOT
from src.pipeline import Pipeline, PipelineError, SkipLesson
from tests.helpers import CountingLLM, make_temp_config

SENTINEL = "TOPSECRETKEY_XYZ"


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):  # noqa: D102
        self.lines.append(self.format(record))


class PipelineBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.llm = CountingLLM()
        self.config = make_temp_config(Path(self.tmp.name), ROOT)
        self.pipeline = Pipeline(self.config, client=self.llm)


class TestPipelineProduction(PipelineBase):
    def test_day1_writes_lessons_001_md(self):
        out = self.pipeline.run(1)
        self.assertTrue(out.exists())
        text = out.read_text(encoding="utf-8")
        self.assertIn("# Giorno 1", text)
        self.assertIn("## Fonte primaria", text)
        self.assertIn("## Fonti", text)

    def test_day7_writes_lessons_007_md(self):
        out = self.pipeline.run(7)
        self.assertEqual(out.name, "007.md")
        self.assertIn("# Giorno 7", out.read_text(encoding="utf-8"))

    def test_day30_writes_lessons_030_md(self):
        out = self.pipeline.run(30)
        self.assertEqual(out.name, "030.md")

    def test_invalid_day_fails_loudly(self):
        with self.assertRaises(PipelineError):
            self.pipeline.run(31)


class TestNoOverwrite(PipelineBase):
    def test_existing_lesson_is_not_overwritten(self):
        out = self.pipeline.run(1)
        original = out.read_text(encoding="utf-8")

        with self.assertRaises(SkipLesson):
            self.pipeline.run(1)
        self.assertEqual(out.read_text(encoding="utf-8"), original)

    def test_force_overwrites(self):
        out = self.pipeline.run(1)
        self.assertTrue(out.exists())
        # --force must not raise SkipLesson and must rewrite the file.
        self.pipeline.run(1, force=True)
        self.assertTrue(out.read_text(encoding="utf-8").strip().startswith("# Giorno 1"))


class TestCaching(PipelineBase):
    def test_research_and_critique_are_cached(self):
        self.pipeline.run(1)
        research_cache = self.config.cache_dir / "research" / "day_001.json"
        critique_cache = self.config.cache_dir / "critique" / "day_001.json"
        self.assertTrue(research_cache.exists())
        self.assertTrue(critique_cache.exists())

    def test_cached_research_is_reused(self):
        self.pipeline.run(1)
        calls_after_first = len(self.llm.calls)
        # Delete the lesson so the pipeline proceeds past the skip check.
        (self.config.output_dir / "001.md").unlink()
        self.pipeline.run(1)
        # With research cached, the researcher should not be called again.
        new_prompt = "".join(u for _, u in self.llm.calls[calls_after_first:])
        self.assertNotIn("Materiale web raccolto", new_prompt)


class TestKeyLeak(PipelineBase):
    def test_api_key_never_appears_in_logs_or_repr(self):
        os.environ["OPENROUTER_API_KEY"] = SENTINEL
        try:
            capture = _Capture()
            logger = logging.getLogger("phylosophy")
            previous = logger.level
            logger.setLevel(logging.DEBUG)
            logger.addHandler(capture)
            try:
                self.pipeline.run(1)
                # Deliberately log the full config repr: the key must not leak.
                logger.warning("config=%r", self.pipeline.config)
            finally:
                logger.removeHandler(capture)
                logger.setLevel(previous)
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
        joined = "\n".join(capture.lines)
        self.assertNotIn(SENTINEL, joined)
        self.assertIn("api_key", joined)


if __name__ == "__main__":
    unittest.main()