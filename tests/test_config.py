"""Config tests: loading, .env parsing, and no API-key leakage."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from src.config import Config, ConfigError, load_config

SENTINEL = "SUPERSECRETKEY123456"


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_loads_defaults_from_yaml(self):
        cfg = load_config(
            config_path=self._write(
                "config.yaml",
                """
llm:
  provider: openrouter
  researcher: {model: m1}
  writer: {model: m3, max_tokens: 999}
search:
  provider: bing
  max_results: 7
generation:
  output_dir: out
curriculum:
  file: curriculum/property.yaml
""",
            ),
            env_path=self._write(".env", "SOMETHING=abc"),
            root=self.root,
        )
        self.assertEqual(cfg.llm_provider, "openrouter")
        self.assertEqual(cfg.researcher.model, "m1")
        self.assertEqual(cfg.writer.max_tokens, 999)
        self.assertEqual(cfg.search_provider, "bing")
        self.assertEqual(cfg.search_max_results, 7)
        self.assertEqual(cfg.output_dir, Path("out"))

    def test_missing_config_raises(self):
        with self.assertRaises(ConfigError):
            load_config(config_path=self.root / "nope.yaml", root=self.root)

    def test_env_loaded_into_environment(self):
        self._write(".env", f"OPENROUTER_API_KEY={SENTINEL}\nOTHER=zzz")
        os.environ.pop("OPENROUTER_API_KEY", None)
        load_config(
            config_path=self._write("config.yaml", "llm: {provider: openrouter}\n"),
            env_path=self.root / ".env",
            root=self.root,
        )
        self.assertEqual(os.environ.get("OPENROUTER_API_KEY"), SENTINEL)

    def test_key_never_printed(self):
        os.environ["OPENROUTER_API_KEY"] = SENTINEL
        try:
            cfg = load_config(
                config_path=self._write("config.yaml", "llm: {provider: openrouter}\n"),
                root=self.root,
            )
            summary = str(cfg.safe_summary())
            reprd = repr(cfg)
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
        self.assertNotIn(SENTINEL, summary)
        self.assertNotIn(SENTINEL, reprd)
        self.assertIn("api_key", summary)

    def test_key_value_not_stored_in_config(self):
        os.environ["OPENROUTER_API_KEY"] = SENTINEL
        try:
            cfg = load_config(
                config_path=self._write("config.yaml", "llm: {provider: openrouter}\n"),
                root=self.root,
            )
            dumped = yaml.safe_dump(cfg.safe_summary())
        finally:
            os.environ.pop("OPENROUTER_API_KEY", None)
        self.assertNotIn(SENTINEL, dumped)


if __name__ == "__main__":
    unittest.main()