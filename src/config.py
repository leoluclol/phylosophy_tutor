"""Configuration loading.

* `.env` is parsed by a tiny parser (no external dependency).
* `config.yaml` holds everything else.
* API keys NEVER live in config.yaml and are NEVER stored inside the Config
  object: the LLM client reads the key from the environment at call time.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_ENV_PATH = ROOT / ".env"
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


class ConfigError(Exception):
    """Raised when the configuration is missing or malformed."""


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file into a dict."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _deep_get(data: Any, dotted: str, default: Any = None) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


class RoleConfig:
    """LLM settings for a single pipeline role (researcher/critic/writer)."""

    __slots__ = ("model", "temperature", "max_tokens")

    def __init__(self, model: str, temperature: float, max_tokens: int):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def __repr__(self) -> str:
        return f"RoleConfig(model={self.model!r}, temperature={self.temperature}, max_tokens={self.max_tokens})"


class Config:
    """Validated view over config.yaml + the resolved project layout."""

    def __init__(self, raw: dict[str, Any], root: Path):
        self._raw = raw
        self.root = root

        self.llm_provider = str(_deep_get(raw, "llm.provider", "openrouter"))
        self.llm_base_url = str(
            _deep_get(raw, "llm.base_url", "https://openrouter.ai/api/v1/chat/completions")
        )
        self.llm_api_key_env = str(_deep_get(raw, "llm.api_key_env", "OPENROUTER_API_KEY"))

        self.researcher = self._role("llm.researcher", "openai/gpt-4o-mini", 0.3, 8192)
        self.critic = self._role("llm.critic", "openai/gpt-4o-mini", 0.2, 6144)
        self.writer = self._role("llm.writer", "openai/gpt-4o-mini", 0.5, 14000)
        self.max_json_retries = int(_deep_get(raw, "llm.max_json_retries", 2))
        self.max_writer_attempts = int(_deep_get(raw, "llm.max_writer_attempts", 2))

        self.search_enabled = bool(_deep_get(raw, "search.enabled", True))
        self.search_provider = str(_deep_get(raw, "search.provider", "duckduckgo"))
        self.search_providers = list(_deep_get(raw, "search.providers", ["duckduckgo", "bing"]))
        self.search_max_results = int(_deep_get(raw, "search.max_results", 10))
        self.search_max_fetch = int(_deep_get(raw, "search.max_fetch", 8))
        self.search_max_page_chars = int(_deep_get(raw, "search.max_page_chars", 12000))
        self.search_fetch_timeout = float(_deep_get(raw, "search.fetch_timeout", 25))
        self.search_max_material_chars = int(_deep_get(raw, "search.max_material_chars", 90000))

        self.curriculum_file = Path(str(_deep_get(raw, "curriculum.file", "curriculum/property.yaml")))
        self.month = str(_deep_get(raw, "curriculum.month", "property"))

        self.output_dir = Path(str(_deep_get(raw, "generation.output_dir", "lessons")))
        self.cache_dir = Path(str(_deep_get(raw, "generation.cache_dir", ".cache")))

        self.log_level = str(_deep_get(raw, "logging.level", "INFO"))

    def _role(self, dotted: str, default_model: str, default_temp: float, default_tokens: int) -> RoleConfig:
        model = str(_deep_get(self._raw, f"{dotted}.model", default_model))
        temperature = float(_deep_get(self._raw, f"{dotted}.temperature", default_temp))
        max_tokens = int(_deep_get(self._raw, f"{dotted}.max_tokens", default_tokens))
        return RoleConfig(model, temperature, max_tokens)

    @property
    def curriculum_path(self) -> Path:
        p = Path(self.curriculum_file)
        return p if p.is_absolute() else self.root / p

    # The key itself is never stored; only the env var name is available.
    def api_key(self) -> Optional[str]:
        key = os.environ.get(self.llm_api_key_env, "").strip()
        return key or None

    def safe_summary(self) -> dict[str, Any]:
        """A redacted summary safe to log/display."""
        return {
            "llm_provider": self.llm_provider,
            "models": {role: c.model for role, c in (("researcher", self.researcher), ("critic", self.critic), ("writer", self.writer))},
            "search_provider": self.search_provider,
            "search_enabled": self.search_enabled,
            "curriculum": str(self.curriculum_path),
            "output_dir": str(self.output_dir),
            "cache_dir": str(self.cache_dir),
            "api_key": "<set>" if self.api_key() else "<missing>",
        }

    def __repr__(self) -> str:
        return f"Config({self.safe_summary()})"


def load_config(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
    root: Optional[Path] = None,
) -> Config:
    root = root or ROOT
    config_path = config_path or DEFAULT_CONFIG_PATH
    env_path = env_path or DEFAULT_ENV_PATH

    # 1) Load .env values into os.environ (do not override existing vars).
    for key, value in load_dotenv(env_path).items():
        os.environ.setdefault(key, value)

    # 2) Load config.yaml.
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")
    with config_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Config(raw, root)