#!/bin/bash
# Scheduled entry point for the unified SQLite campaign pipeline.
# It is dry-run by default; production scheduling must pass --live and set
# SMS_LIVE_APPROVED=true in the server-only environment.
set -euo pipefail

exec uv run python scripts/run_sqlite_campaigns.py "$@"
