#!/bin/bash
# Quick run script for SMS Campaign Manager

echo "=================================================="
echo "Step 1: Syncing Opt-Outs from Twilio..."
echo "=================================================="
uv run python -m sms_campaign.sync_opt_outs

if [ $? -eq 0 ]; then
    echo ""
    echo "=================================================="
    echo "Step 2: Starting Campaign Manager..."
    echo "=================================================="
    uv run python -m sms_campaign.cli "$@"
else
    echo "Opt-out sync failed. Aborting campaign to prevent sending to opted-out customers."
    exit 1
fi
