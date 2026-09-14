#!/usr/bin/env python3
"""Safely run campaigns from the unified SQLite database.

The command is dry-run by default.  Live sending requires ``--live`` and
``SMS_LIVE_APPROVED=true`` in the server environment, plus valid Twilio
credentials.  Credentials are never accepted as command-line arguments.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from mirror_to_sheets import ensure_access_token, mirror_all
from sms_campaign.data_store import ZeyDataStore
from sms_campaign.services.sms_sender import SMSSender
from sms_campaign.sqlite_campaigns import SQLiteCampaignRunner
from sms_campaign.utils.config import Config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--database", type=Path, default=root / "data" / "customer_master.sqlite3")
    parser.add_argument("--campaign-id", type=int, help="Run one campaign row only")
    parser.add_argument("--test-phone", action="append", default=[], help="Restrict recipients")
    parser.add_argument("--max-messages", type=int, help="Cap messages in this run")
    parser.add_argument("--live", action="store_true", help="Actually send through Twilio")
    parser.add_argument("--mirror", action="store_true", help="Mirror SQLite tables to Google Sheets after a live run")
    args = parser.parse_args()

    config = Config(env_file=root / ".env", config_file=root / "config" / "config.yml")
    if args.live and os.getenv("SMS_LIVE_APPROVED", "").lower() != "true":
        parser.error("live sending requires SMS_LIVE_APPROVED=true in the server environment")
    if args.live:
        valid, errors = config.validate()
        if not valid:
            parser.error("; ".join(errors))

    sender = SMSSender(
        account_sid=config.twilio_account_sid,
        auth_token=config.twilio_auth_token,
        from_phone=config.twilio_phone_number,
        api_key=config.twilio_api_key,
        api_secret=config.twilio_api_secret,
        dry_run=not args.live,
        rate_limit_delay=config.sms_rate_limit_delay,
    )
    runner = SQLiteCampaignRunner(
        ZeyDataStore(args.database),
        sender,
        test_phones=args.test_phone,
        max_messages=args.max_messages,
    )
    campaigns = runner.pending_campaigns()
    if args.campaign_id is not None:
        campaigns = [
            campaign for campaign in campaigns
            if int(campaign.raw_data["campaign_id"]) == args.campaign_id
        ]
    results = [
        runner.run_campaign(
            campaign,
            campaign_id=int(campaign.raw_data["campaign_id"]),
        )
        for campaign in campaigns
    ]
    output: dict[str, object] = {"campaigns": [result.__dict__ for result in results]}
    if args.mirror:
        if not args.live:
            parser.error("--mirror requires --live so the sheet contains only durable send results")
        ensure_access_token()
        output["mirror"] = mirror_all(runner.store)
    print(json.dumps(output, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
