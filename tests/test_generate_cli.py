"""CLI tests for generate.py (patching the pipeline object)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import generate
from src.pipeline import PipelineError, SkipLesson


def _fake_pipeline_class(run_impl, calls: list):
    """Build a Pipeline stand-in: a class whose run() captures its arguments."""

    class FakePipeline:
        def __init__(self, config):
            self.config = config

        def run(self, day, force=False):
            calls.append((day, force))
            return run_impl()

    return FakePipeline


class TestCLI(unittest.TestCase):
    def test_day_argument_reaches_pipeline(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "005.md"
            cls = _fake_pipeline_class(lambda: target, calls)
            with mock.patch.object(generate, "Pipeline", cls):
                rc = generate.main(["--config", "config.yaml", "--day", "5"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [(5, False)])

    def test_force_flag_is_forwarded(self):
        calls = []
        cls = _fake_pipeline_class(lambda: Path("lessons") / "003.md", calls)
        with mock.patch.object(generate, "Pipeline", cls):
            rc = generate.main(["--day", "3", "--force"])
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [(3, True)])

    def test_existing_lesson_is_not_an_error(self):
        calls = []
        cls = _fake_pipeline_class(lambda: (_ for _ in ()).throw(SkipLesson()), calls)
        with mock.patch.object(generate, "Pipeline", cls):
            rc = generate.main(["--config", "config.yaml", "--day", "3"])
        self.assertEqual(rc, 0)

    def test_pipeline_failure_returns_nonzero(self):
        calls = []
        cls = _fake_pipeline_class(lambda: (_ for _ in ()).throw(PipelineError("boom")), calls)
        with mock.patch.object(generate, "Pipeline", cls):
            rc = generate.main(["--config", "config.yaml", "--day", "9"])
        self.assertEqual(rc, 1)

    def test_missing_day_is_rejected(self):
        calls = []
        cls = _fake_pipeline_class(lambda: Path("x"), calls)
        with mock.patch.object(generate, "Pipeline", cls):
            with self.assertRaises(SystemExit) as ctx:
                generate.main(["--config", "config.yaml"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()