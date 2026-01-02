#!/bin/bash
# Sync opt-outs from Twilio to customer list

cd "$(dirname "$0")"
uv run python sync_opt_outs.py "$@"
