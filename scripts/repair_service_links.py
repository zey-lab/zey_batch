#!/usr/bin/env python3
"""One-off repair (2026-10-07) for services/transactions saved before two fixes:

1. booking_status: fill it from the latest stored appointment webhook for
   each appointment (the field was received but never saved).
2. Orphans: re-link services/transactions saved with no customer to the
   customer they reference, now that it exists (ZeyDataStore.relink_orphans).

Only rows whose booking_status / customer_id are still NULL are touched. The
affected rows' ids are written to data/backups/ first. Dry run by default;
pass --apply to commit.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sms_campaign.data_store import ZeyDataStore  # noqa: E402
from sms_campaign.db import get_connection  # noqa: E402

BACKFILL_STATUS = """
UPDATE services s SET booking_status = w.status
FROM (
    SELECT DISTINCT ON (appt_id) appt_id,
           COALESCE(status, CASE WHEN action = 'deleted' THEN 'Deleted' END) AS status
    FROM (
        SELECT j->'payload'->>'appointmentId' AS appt_id,
               j->'payload'->>'bookingStatus' AS status,
               action, received_at
        FROM (
            SELECT CASE WHEN pg_input_is_valid(payload_json, 'jsonb')
                        THEN payload_json::jsonb END AS j,
                   action, received_at
            FROM webhook_events WHERE event_type = 'appointment'
        ) parsed
    ) e
    WHERE appt_id IS NOT NULL
    ORDER BY appt_id, received_at DESC
) w
WHERE s.vagaro_appt_id = w.appt_id AND s.booking_status IS NULL AND w.status IS NOT NULL
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="commit the changes (default: dry run)")
    args = parser.parse_args()

    conn = get_connection()
    try:
        before = {
            "services_null_customer": [
                r["service_id"] for r in conn.execute(
                    "SELECT service_id FROM services WHERE customer_id IS NULL ORDER BY 1").fetchall()
            ],
            "transactions_null_customer": [
                r["transaction_id"] for r in conn.execute(
                    "SELECT transaction_id FROM transactions WHERE customer_id IS NULL ORDER BY 1").fetchall()
            ],
            "services_null_status": [
                r["service_id"] for r in conn.execute(
                    "SELECT service_id FROM services WHERE booking_status IS NULL ORDER BY 1").fetchall()
            ],
        }
        if args.apply:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = ROOT / "data" / "backups" / f"repair_service_links_{stamp}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps(before))
        statuses = conn.execute(BACKFILL_STATUS).rowcount
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    if not args.apply:
        print(json.dumps({"dry_run": True, "status_rows_to_fill": statuses,
                          "orphan_services": len(before["services_null_customer"]),
                          "orphan_transactions": len(before["transactions_null_customer"])}))
        return

    relinked = ZeyDataStore().relink_orphans()
    print(json.dumps({"applied": True, "status_rows_filled": statuses,
                      "relinked": relinked, "backup": str(backup)}))


if __name__ == "__main__":
    main()
