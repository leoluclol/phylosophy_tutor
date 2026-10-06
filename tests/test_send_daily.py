"""Tests for send_daily.py (Markdown->Telegram conversion, day state, packing)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import send_daily as sd


class TestInlineConversion(unittest.TestCase):
    def test_bold_italic_code(self):
        self.assertEqual(sd.inline_to_html("**bold** _ita_ `code`"), "<b>bold</b> <i>ita</i> <code>code</code>")

    def test_nested_bold_inside_paragraph(self):
        self.assertEqual(
            sd.inline_to_html("**Fonte:** John, **Opera:** X"),
            "<b>Fonte:</b> John, <b>Opera:</b> X",
        )

    def test_urls_become_links(self):
        self.assertEqual(
            sd.inline_to_html("vedi https://plato.stanford.edu/entries/x/"),
            'vedi <a href="https://plato.stanford.edu/entries/x/">https://plato.stanford.edu/entries/x/</a>',
        )

    def test_escapes_html_special_chars(self):
        self.assertEqual(sd.inline_to_html("a < b & c > d"), "a &lt; b &amp; c &gt; d")

    def test_markdown_links(self):
        self.assertEqual(
            sd.inline_to_html("[SEP](https://se.example/)"),
            '<a href="https://se.example/">SEP</a>',
        )


class TestMarkdownBlocks(unittest.TestCase):
    def test_headings(self):
        blocks = sd.markdown_to_telegram_blocks("# Titolo\n\n## Sezione\n\n### Sotto")
        self.assertEqual(blocks[0], "<b>Titolo</b>")
        self.assertEqual(blocks[1], "▎<b>Sezione</b>")
        self.assertEqual(blocks[2], "▎<b>Sotto</b>")

    def test_bullets_grouped(self):
        md = "- uno\n- due\n- tre"
        blocks = sd.markdown_to_telegram_blocks(md)
        self.assertEqual(blocks, ["• uno\n• due\n• tre"])

    def test_numbered_list_keeps_numbers(self):
        md = "1. prima\n2. seconda"
        blocks = sd.markdown_to_telegram_blocks(md)
        self.assertEqual(blocks, ["1. prima\n2. seconda"])

    def test_paragraph_ws_inline(self):
        md = "Solo testo **grassetto** qui."
        blocks = sd.markdown_to_telegram_blocks(md)
        self.assertEqual(blocks, ["Solo testo <b>grassetto</b> qui."])

    def test_blank_between_blocks(self):
        md = "Par1\n\nPar2"
        self.assertEqual(sd.markdown_to_telegram_blocks(md), ["Par1", "Par2"])

    def test_blockquotes_grouped(self):
        md = "> Prima citazione\n> Seconda riga\n\nTesto"
        blocks = sd.markdown_to_telegram_blocks(md)
        self.assertEqual(blocks[0], "<blockquote>Prima citazione\nSeconda riga</blockquote>")
        self.assertEqual(blocks[1], "Testo")


class TestPacking(unittest.TestCase):
    def test_splits_at_limit(self):
        blocks = [f"b{i}" for i in range(10)]
        msgs = sd.pack_blocks(blocks, max_chars=20)
        self.assertGreater(len(msgs), 1)
        for m in msgs:
            self.assertLessEqual(len(m), 20)

    def test_empty_blocks_dropped(self):
        self.assertEqual(sd.pack_blocks(["", "  ", "x"], max_chars=50), ["x"])

    def test_build_messages_full_lesson(self):
        md = ("# Giorno 1 — Test\n\n## Obiettivi\n- a\n- b\n\n" * 5)
        msgs = sd.build_messages(md, max_chars=100)
        self.assertTrue(msgs)
        for m in msgs:
            self.assertLessEqual(len(m), 100)


class TestDayState(unittest.TestCase):
    def test_no_state_starts_at_one(self):
        self.assertEqual(sd.resolve_next_day({}, 30), 1)

    def test_advances_from_state(self):
        self.assertEqual(sd.resolve_next_day({"last_day": 7}, 30), 8)

    def test_override_wins(self):
        self.assertEqual(sd.resolve_next_day({"last_day": 7}, 30, override=3), 3)

    def test_override_out_of_range(self):
        with self.assertRaises(ValueError):
            sd.resolve_next_day({}, 30, override=31)

    def test_course_complete_without_loop(self):
        with self.assertRaises(sd.CourseComplete):
            sd.resolve_next_day({"last_day": 30}, 30)

    def test_course_complete_loops(self):
        self.assertEqual(sd.resolve_next_day({"last_day": 30}, 30, loop=True), 1)

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            sd.save_state(path, 12)
            self.assertEqual(sd.read_state(path), {"last_day": 12})


class TestMainDryRun(unittest.TestCase):
    def _write_lesson(self, tmp: str) -> Path:
        root = Path(tmp)
        lessons = root / "lessons"
        lessons.mkdir(parents=True)
        (lessons / "003.md").write_text("# Giorno 3 — Test\n\n## Obiettivi\n- x\n", encoding="utf-8")
        return root

    def test_dry_run_generates_and_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_lesson(tmp)
            env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_USER_ID": "42"}
            with mock.patch.object(sd, "load_config") as cfg, \
                 mock.patch.object(sd.generate, "main", return_value=0), \
                 mock.patch.dict("os.environ", env, clear=True):
                config = mock.Mock()
                config.root = root
                config.output_dir = "lessons"
                config.curriculum_path = root / "curriculum.yml"
                cfg.return_value = config
                with mock.patch("send_daily.get_max_day", return_value=30):
                    rc = sd.main([
                        "--day", "3", "--dry-run",
                        "--state", str(root / "state.json"),
                    ])
            self.assertEqual(rc, 0)

    def test_missing_telegram_env_is_config_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(sd, "load_config") as cfg, \
                 mock.patch.dict("os.environ", {}, clear=True):
                config = mock.Mock()
                config.root = root
                config.curriculum_path = root / "c.yml"
                cfg.return_value = config
                with mock.patch("send_daily.get_max_day", return_value=30):
                    rc = sd.main(["--day", "1", "--dry-run"])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()