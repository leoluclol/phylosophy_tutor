#!/usr/bin/env python3
"""Generate tomorrow's philosophy lesson and deliver it via Telegram.

Designed to be run every day from cron at 05:00 (VPS time; see `daily_lesson.sh`
and `crontab.example`). Flow:

    1. Load config + `.env` (reads TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID).
    2. Resolve which lesson day comes next (persisted in .cache/daily_state.json;
       `--day` overrides, `--loop` restarts after the 30-day course).
    3. Run the normal generate.py pipeline for that day (existing lessons are
       reused, pass `--force` to regenerate).
    4. Convert the lesson Markdown into Telegram-compatible HTML.
    5. Split the lesson into ≤3800-char messages and send each over Telegram.

Exit codes: 0 ok, 1 runtime failure, 2 configuration error.

Usage:
    python send_daily.py                # next lesson, then send
    python send_daily.py --day 15       # force a specific day
    python send_daily.py --force        # regenerate today's lesson file
    python send_daily.py --dry-run      # generate + format, print, do not send
    python send_daily.py --loop         # restart at day 1 when the course ends
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests

import generate
from src.config import ROOT, ConfigError, load_config
from src.models import CurriculumMonth

logger = logging.getLogger("phylosophy.send_daily")

STATE_FILE = ".cache/daily_state.json"
MAX_MESSAGE_CHARS = 3800  # Telegram hard limit is 4096; stay under it.
SEND_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Markdown -> Telegram HTML
# ---------------------------------------------------------------------------

_INLINE_RE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<bold>\*\*[^*\n]+\*\*)"
    r"|(?P<ital>\*[^*\n]+\*|_[^_\n]+_)"
    r"|(?P<link>\[[^\]\n]+\]\([^)\n]+\))"
    r"|(?P<url>https?://[^\s<>()\"']+)"
)


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _strip_url_punct(url: str) -> str:
    return url.rstrip(".,;:!?)]}")


def inline_to_html(text: str) -> str:
    """Convert inline Markdown (bold/italic/code/links/URLs) to Telegram HTML."""
    out: list[str] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append(_escape_html(text[pos : m.start()]))
        kind = m.lastgroup
        token = m.group()
        if kind == "code":
            out.append("<code>%s</code>" % _escape_html(token[1:-1]))
        elif kind == "bold":
            out.append("<b>%s</b>" % inline_to_html(token[2:-2]))
        elif kind == "ital":
            out.append("<i>%s</i>" % inline_to_html(token[1:-1]))
        elif kind == "link":
            label, url = token[1:-1].rsplit("](", 1)
            out.append('<a href="%s">%s</a>' % (_escape_attr(url), _escape_html(label)))
        elif kind == "url":
            url = _strip_url_punct(token)
            out.append('<a href="%s">%s</a>' % (_escape_attr(url), _escape_html(url)))
        pos = m.end()
    if pos < len(text):
        out.append(_escape_html(text[pos:]))
    return "".join(out)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")


def markdown_to_telegram_blocks(md_text: str) -> list[str]:
    """Split lesson Markdown into a list of self-contained Telegram HTML blocks."""
    blocks: list[str] = []
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        m = _HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            text = inline_to_html(m.group(2))
            blocks.append("<b>%s</b>" % text if level == 1 else "▎<b>%s</b>" % text)
            i += 1
            continue

        if _HR_RE.match(stripped):
            blocks.append("─" * 20)
            i += 1
            continue

        m = _BULLET_RE.match(stripped)
        if m:
            items: list[str] = []
            while i < len(lines):
                b = _BULLET_RE.match(lines[i].strip())
                if not b:
                    break
                items.append("• " + inline_to_html(b.group(1)))
                i += 1
            blocks.append("\n".join(items))
            continue

        m = _QUOTE_RE.match(stripped)
        if m:
            items = []
            while i < len(lines):
                q = _QUOTE_RE.match(lines[i].strip())
                if not q:
                    break
                items.append(inline_to_html(q.group(1)))
                i += 1
            blocks.append("<blockquote>%s</blockquote>" % "\n".join(items))
            continue

        m = _NUMBERED_RE.match(stripped)
        if m:
            items = []
            while i < len(lines):
                n = _NUMBERED_RE.match(lines[i].strip())
                if not n:
                    break
                items.append(f"{n.group(1)}. " + inline_to_html(n.group(2)))
                i += 1
            blocks.append("\n".join(items))
            continue

        # Paragraph: consume consecutive text lines until blank / heading / list / hr.
        para = [stripped]
        i += 1
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                break
            if (
                _HEADING_RE.match(line)
                or _BULLET_RE.match(line)
                or _NUMBERED_RE.match(line)
                or _QUOTE_RE.match(line)
                or _HR_RE.match(line)
            ):
                break
            para.append(line)
            i += 1
        blocks.append("\n".join(inline_to_html(p) for p in para))

    return blocks


def pack_blocks(blocks: list[str], max_chars: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Join HTML blocks into messages each below the Telegram size limit."""
    messages: list[str] = []
    current = ""
    for block in blocks:
        sep = "\n\n" if current else ""
        block = block.strip()
        if not block:
            continue
        if len(current) + len(sep) + len(block) > max_chars:
            if current:
                messages.append(current)
            current = block
        else:
            current = current + sep + block
    if current:
        messages.append(current)
    return messages


def build_messages(md_text: str, max_chars: int = MAX_MESSAGE_CHARS) -> list[str]:
    """Full Markdown -> chunked Telegram-HTML conversion for one lesson."""
    blocks = markdown_to_telegram_blocks(md_text)
    # Safety net: never let a single block exceed the message budget.
    flat: list[str] = []
    for block in blocks:
        if len(block) <= max_chars:
            flat.append(block)
        else:
            flat.extend(seg for seg in block.split("\n") if seg.strip())
    return pack_blocks(flat, max_chars)


# ---------------------------------------------------------------------------
# Day progression (state file)
# ---------------------------------------------------------------------------


class CourseComplete(Exception):
    def __init__(self, max_day: int):
        super().__init__(f"course complete after day {max_day}")
        self.max_day = max_day


def read_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state_path: Path, day: int) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"last_day": day}) + "\n", encoding="utf-8")


def resolve_next_day(
    state: dict,
    max_day: int,
    override: int | None = None,
    loop: bool = False,
) -> int:
    if override is not None:
        if override < 1 or override > max_day:
            raise ValueError(f"--day {override} is out of range (1..{max_day})")
        return override
    last = state.get("last_day", 0) or 0
    if not isinstance(last, int):
        last = 0
    nxt = last + 1
    if nxt > max_day:
        if loop:
            return 1
        raise CourseComplete(max_day)
    return nxt


def get_max_day(config) -> int:
    raw = __import__("yaml").safe_load(config.curriculum_path.read_text(encoding="utf-8")) or {}
    if isinstance(raw, dict) and "month" in raw:
        raw = raw["month"]
    return len(CurriculumMonth.model_validate(raw).days)


# ---------------------------------------------------------------------------
# Telegram delivery
# ---------------------------------------------------------------------------


def send_message(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data: dict = {}
    last_err = "unknown error"
    for attempt in range(3):
        try:
            resp = requests.post(url, data=payload, timeout=SEND_TIMEOUT)
            data = resp.json()
        except requests.RequestException as exc:
            last_err = f"network error: {exc}"
            time.sleep(2 * (attempt + 1))
            continue
        except ValueError:
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            time.sleep(2 * (attempt + 1))
            continue
        if data.get("ok"):
            return
        last_err = f"HTTP {resp.status_code}: {data.get('description')}"
        if resp.status_code in (429, 500, 502, 503, 504, 408):
            time.sleep(5 * (attempt + 1))
            continue
        break
    raise RuntimeError(last_err)


def send_messages(token: str, chat_id: str, messages: list[str]) -> None:
    for idx, text in enumerate(messages, 1):
        send_message(token, chat_id, text)
        logger.info("  sent %d/%d (%d chars)", idx, len(messages), len(text))
        time.sleep(0.4)  # be polite to the Bot API


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="send_daily.py",
        description="Generate the next philosophy lesson and send it over Telegram.",
    )
    parser.add_argument("--day", type=int, default=None, help="Override: send this lesson day (1..N).")
    parser.add_argument("--force", action="store_true", help="Regenerate the lesson even if the file exists.")
    parser.add_argument("--loop", action="store_true", help="Restart at day 1 once the course is complete.")
    parser.add_argument("--dry-run", action="store_true", help="Generate and format but do not send.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml.")
    parser.add_argument("--env", type=Path, default=None, help="Path to .env file.")
    parser.add_argument("--state", type=Path, default=None, help="State file path (default: .cache/daily_state.json).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        config = load_config(config_path=args.config, env_path=args.env)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_USER_ID", "").strip()
    if not token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_USER_ID must be set in .env")
        return 2

    root = getattr(config, "root", ROOT)
    state_path = args.state if args.state else _resolve(root, STATE_FILE)
    max_day = get_max_day(config)

    state = read_state(state_path)
    try:
        day = resolve_next_day(state, max_day, override=args.day, loop=args.loop)
    except CourseComplete as exc:
        logger.info("Course complete after day %d — nothing to do.", exc.max_day)
        return 0
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    # Generate (or reuse) the lesson for this day.
    gen_args: list[str] = ["--day", str(day)]
    if args.force:
        gen_args.append("--force")
    if args.config:
        gen_args += ["--config", str(args.config)]
    if args.env:
        gen_args += ["--env", str(args.env)]
    if generate.main(gen_args) != 0:
        return 1

    lesson_dir = _resolve(root, config.output_dir)
    lesson_path = lesson_dir / f"{day:03d}.md"
    if not lesson_path.exists():
        logger.error("Lesson file missing after generation: %s", lesson_path)
        return 1

    messages = build_messages(lesson_path.read_text(encoding="utf-8"))
    if not messages:
        logger.error("No message content produced for day %d.", day)
        return 1
    logger.info(
        "Day %d — %d message(s), %d chars total.",
        day,
        len(messages),
        sum(len(m) for m in messages),
    )

    if args.dry_run:
        for idx, msg in enumerate(messages, 1):
            print(f"--- [{idx}/{len(messages)}] ({len(msg)} chars) ---")
            print(msg)
        return 0

    try:
        send_messages(token, chat_id, messages)
    except RuntimeError as exc:
        logger.error("Telegram send failed: %s", exc)
        return 1

    save_state(state_path, day)
    logger.info("Sent lesson day %d (%s); state updated to day %d.", day, lesson_path.name, day)
    return 0


def _resolve(root: Path, path: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else root / path


if __name__ == "__main__":
    sys.exit(main())