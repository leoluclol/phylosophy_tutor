#!/usr/bin/env python3
"""Philosophy Tutor — CLI.

Usage:
    python generate.py --day 1
    python generate.py --day 15 --force
    python generate.py --day 30 --provider duckduckgo

The command generates lessons/001.md (or the requested day) by running the
curriculum -> research -> critique -> writer pipeline.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.config import ConfigError, load_config
from src.pipeline import Pipeline, PipelineError, SkipLesson

logger = logging.getLogger("phylosophy")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate.py",
        description="Generate one philosophy lesson from the curriculum.",
    )
    parser.add_argument("--day", type=int, required=True, help="Day to generate (1..30).")
    parser.add_argument("--force", action="store_true", help="Regenerate even if files exist.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml.")
    parser.add_argument("--env", type=Path, default=None, help="Path to .env file.")
    parser.add_argument("--provider", type=str, default=None,
                        help="Override the search provider (duckduckgo|bing|multi).")
    parser.add_argument("--debug", action="store_true", help="Verbose logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(config_path=args.config, env_path=args.env)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    if args.provider:
        config.search_provider = args.provider

    logging.basicConfig(
        level=logging.DEBUG if args.debug else config.log_level,
        format=("%(levelname)s %(name)s: %(message)s" if args.debug else "%(message)s"),
    )

    pipeline = Pipeline(config)
    try:
        pipeline.run(args.day, force=args.force)
    except SkipLesson:
        return 0
    except (PipelineError, Exception) as exc:  # noqa: BLE001 - readable top-level failures
        logger.error("Pipeline failed: %s", exc)
        if args.debug:
            raise
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())