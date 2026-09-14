#!/usr/bin/env python3
"""Preview or run email campaigns from the durable SQLite database.

The command is always a preview unless ``--live`` is supplied.  Live mode
also requires ``EMAIL_LIVE_APPROVED=true`` and a server-side Gmail OAuth
configuration.  Email credentials are never accepted as command-line args.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sms_campaign.data_store import ZeyDataStore
from sms_campaign.services.email_sender import EmailSender
from sms_campaign.sqlite_campaigns import EmailCampaignRunner
from sms_campaign.utils.config import Config


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=root / "data" / "customer_master.sqlite3")
    parser.add_argument("--campaign-id", type=int)
    parser.add_argument("--test-email", action="append", default=[], help="Restrict recipients to this address")
    parser.add_argument("--max-messages", type=int)
    parser.add_argument("--live", action="store_true", help="Actually send through Gmail API")
    args = parser.parse_args()

    # Load the protected project .env without ever printing its contents.
    Config(env_file=root / ".env", config_file=root / "config" / "config.yml")

    if args.live and os.getenv("EMAIL_LIVE_APPROVED", "").lower() != "true":
        parser.error("live sending requires EMAIL_LIVE_APPROVED=true in the server environment")
    sender = EmailSender.from_environment(dry_run=not args.live)
    if args.live:
        errors = sender.validate_live_configuration()
        if errors:
            parser.error("; ".join(errors))

    runner = EmailCampaignRunner(
        ZeyDataStore(args.database), sender,
        test_emails=args.test_email,
        max_messages=args.max_messages,
    )
    campaigns = runner.pending_campaigns()
    if args.campaign_id is not None:
        campaigns = [campaign for campaign in campaigns if int(campaign.raw_data["campaign_id"]) == args.campaign_id]
    results = [runner.run_campaign(campaign, campaign_id=int(campaign.raw_data["campaign_id"])) for campaign in campaigns]
    print(json.dumps({"channel": "email", "campaigns": [result.__dict__ for result in results]}, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
