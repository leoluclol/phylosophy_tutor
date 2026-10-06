#!/usr/bin/env bash
# Daily philosophy lesson — cron wrapper.
# Runs send_daily.py every day at 05:00 VPS time (see crontab.example).
#
# The script:
#   * pins the working directory to the project root (cron has no cwd),
#   * uses the project venv Python explicitly (cron has a minimal PATH),
#   * appends all output (stdout+stderr) to a rotating log under logs/.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

# Loop so the service keeps going after the 30-day course ends. Remove --loop
# if you prefer it to stop silently when the curriculum is finished.
exec .venv/bin/python send_daily.py --loop >>"$LOG_DIR/daily.log" 2>&1