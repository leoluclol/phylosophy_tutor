"""LLM client (OpenRouter) and robust JSON helpers.

The LLM is accessed through the Chat Completions API of OpenRouter using
`requests` only. The API key is read from the environment at call time and is
never logged.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional

import requests

from .config import Config, RoleConfig

logger = logging.getLogger("phylosophy")

HEADERS = {
    "Content-Type": "application/json",
}


class LLMError(Exception):
    """Base error for LLM calls."""


class LLMCallError(LLMError):
    """The provider failed (network, HTTP status, empty content)."""


class LLMJSONError(LLMError):
    """The model produced JSON that could not be parsed/validated after retries."""


class LLMClient(ABC):
    """Minimal interface to an LLM."""

    @abstractmethod
    def complete(self, *, system: str, user: str, role: RoleConfig) -> str:
        """Return the raw completion text for the given messages."""


class OpenRouterClient(LLMClient):
    """A thin HTTP client for OpenRouter's /chat/completions endpoint."""

    def __init__(self, config: Config, timeout: int = 300):
        self.config = config
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

    def _headers(self) -> dict[str, str]:
        key = self.config.api_key()
        if not key:
            raise LLMCallError(
                f"Missing API key: set {self.config.llm_api_key_env} in your environment "
                "or in a .env file (see .env.example)."
            )
        return {"Authorization": f"Bearer {key}"}

    def complete(self, *, system: str, user: str, role: RoleConfig) -> str:
        payload = {
            "model": role.model,
            "temperature": role.temperature,
            "max_tokens": role.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = self._session.post(
                self.config.llm_base_url, json=payload, headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:  # noqa: PERF203
            raise LLMCallError(f"LLM request failed: {exc}") from exc

        if resp.status_code != 200:
            raise LLMCallError(f"LLM API returned HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMCallError(f"Malformed LLM response: {resp.text[:300]}") from exc

        if not content or not content.strip():
            raise LLMCallError("LLM returned empty content.")
        return content


# ---------------------------------------------------------------------------
# JSON extraction / validation with bounded retries
# ---------------------------------------------------------------------------


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of free-form model output.

    Tolerates markdown fences, prose before/after, and trailing punctuation.
    Raises ValueError when no JSON can be found.
    """
    if not text:
        raise ValueError("empty model output")

    # Strip markdown / prose fences.
    cleaned = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE).strip()
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    start = cleaned.find("{")
    if start == -1:
        start = cleaned.find("[")
    if start == -1:
        raise ValueError("no JSON object/array found in model output")

    # Find the matching close bracket / brace, tracking nesting and string state.
    open_close = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in open_close:
            stack.append(ch)
        elif ch in open_close.values():
            if not stack:
                raise ValueError("unbalanced closing bracket in model output")
            opener = stack.pop()
            if open_close[opener] != ch:
                raise ValueError("mismatched brackets in model output")
            if not stack:
                return json.loads(cleaned[start : i + 1])
    raise ValueError("unterminated JSON object/array in model output")


def _correction_suffix(error_text: str) -> str:
    return (
        "\n\nThe JSON you just produced could not be validated.\n"
        f"Validation error: {error_text}\n"
        "Please re-read the instructions, produce ONLY a single valid JSON object "
        "(no prose, no markdown fences), and correct all mistakes."
    )


def chat_json(
    client: LLMClient,
    *,
    system: str,
    user: str,
    role: RoleConfig,
    validator: Callable[[dict], Any],
    normalize: Optional[Callable[[dict], dict]] = None,
    max_retries: int = 2,
) -> Any:
    """Call the model and insist on valid JSON matching ``validator``.

    * parse raw text -> dict
    * normalize common LLM shape mistakes
    * validate with ``validator`` (Pydantic)
    On any failure, retry feeding the error back to the model. After exhausting
    retries, raises LLMJSONError with a readable message.
    """
    attempt_user = user
    for attempt in range(max_retries + 1):
        raw = client.complete(system=system, user=attempt_user, role=role)
        try:
            obj = extract_json(raw)
            if not isinstance(obj, dict):
                raise ValueError("expected a JSON object at the top level")
            if normalize is not None:
                obj = normalize(obj)
            return validator(obj)
        except Exception as exc:  # noqa: BLE001 - any failure becomes retriable feedback
            logger.warning("JSON validation failed (attempt %d/%d): %s", attempt + 1, max_retries + 1, exc)
            if attempt >= max_retries:
                raise LLMJSONError(f"Model produced invalid JSON after {max_retries + 1} attempts. Last error: {exc}")
            attempt_user = user + _correction_suffix(str(exc))
    raise LLMJSONError("unreachable")  # pragma: no cover