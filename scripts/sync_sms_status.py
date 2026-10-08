#!/usr/bin/env python3
"""Bring sms_history in line with Twilio. Runs daily before the panel report.

1. Delivery status: send_sms() stores the status Twilio reports a moment
   after the send (usually queued or sent) and nothing updated it afterwards,
   so the panel and the daily report never saw deliveries or carrier
   failures. This fetches the current status of the last week's messages
   (--days to go further back). Carriers confirm most deliveries within a
   minute, some after ~30 min; a message that stays "sent" got no delivery
   receipt from the carrier. Runs after the noon send and before the report.
2. STOP: a customer whose latest SMS was refused because they replied STOP
   (Twilio error 21610) is marked sms_opt_out, so campaigns stop retrying
   them every day. Twilio already blocks those sends, so no customer receives
   anything different; only the wasted attempts stop.
3. Undeliverable numbers: a customer whose latest SMS failed for a reason
   that won't go away (invalid number, landline, inactive number, region
   without SMS permission), or whose last 3 sends all failed, gets
   customers.sms_undeliverable. Campaigns skip them and the panel lists them
   for fixing in Vagaro; a corrected number clears it (customer webhook).

Prints a one-line JSON summary. --dry-run reports what would change.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sms_campaign.data_store import ZeyDataStore  # noqa: E402
from sms_campaign.db import get_connection  # noqa: E402

FINAL_STATUSES = ("delivered", "undelivered", "failed", "canceled", "read")
TWILIO_UNSUBSCRIBED = "unsubscribed recipient"
LOOKBACK_DAYS = 7


def refresh_statuses(client, from_number: str, *, days: int = LOOKBACK_DAYS, dry_run: bool = False) -> dict:
    conn = get_connection()
    try:
        # Recent messages still in flight, plus recent failures stored without
        # Twilio's error code (needed to tell a dead number from a busy one).
        rows = conn.execute(
            "SELECT sms_id, twilio_sid, status, error_message FROM sms_history "
            "WHERE twilio_sid IS NOT NULL AND ("
            "(status <> ALL(%s) AND sent_at::timestamptz >= now() - make_interval(days => %s)) OR "
            "(status IN ('failed', 'undelivered') AND COALESCE(error_message, '') NOT LIKE '%%Twilio error%%' "
            "AND sent_at::timestamptz >= now() - interval '30 days'))",
            (list(FINAL_STATUSES), days),
        ).fetchall()
        if not rows:
            return {"checked": 0, "updated": 0}
        since = datetime.now(timezone.utc) - timedelta(days=days + 1)
        current = {m.sid: m for m in client.messages.list(from_=from_number, date_sent_after=since)}
        changes: dict[str, int] = {}
        for row in rows:
            message = current.get(row["twilio_sid"]) or client.messages(row["twilio_sid"]).fetch()
            needs_code = (message.status in ("failed", "undelivered")
                          and "Twilio error" not in (row["error_message"] or ""))
            if message.status == row["status"] and not needs_code:
                continue
            changes[message.status] = changes.get(message.status, 0) + 1
            if dry_run:
                continue
            error = None
            if message.status in ("failed", "undelivered"):
                error = f"Message status: {message.status} (Twilio error {message.error_code})"
            conn.execute(
                "UPDATE sms_history SET status=%s, error_message=COALESCE(%s, error_message) WHERE sms_id=%s",
                (message.status, error, row["sms_id"]),
            )
        conn.commit()
        return {"checked": len(rows), "updated": sum(changes.values()), "now": changes}
    finally:
        conn.close()


def stopped_customers() -> list[dict]:
    """Customers still opted in whose latest SMS Twilio refused for STOP."""
    conn = get_connection()
    try:
        return conn.execute(
            """WITH latest AS (
                 SELECT DISTINCT ON (customer_id) customer_id, error_message
                 FROM sms_history WHERE customer_id IS NOT NULL
                 ORDER BY customer_id, sent_at::timestamptz DESC)
               SELECT c.customer_id, c.mobile FROM latest l JOIN customers c USING (customer_id)
               WHERE l.error_message LIKE %s AND COALESCE(c.sms_opt_out, 0) <> 1 AND c.mobile IS NOT NULL
               ORDER BY c.customer_id""",
            (f"%{TWILIO_UNSUBSCRIBED}%",),
        ).fetchall()
    finally:
        conn.close()


def sync_stop_opt_outs(store: ZeyDataStore, *, dry_run: bool = False) -> dict:
    customers = stopped_customers()
    if not dry_run:
        for c in customers:
            store.set_opt_out(c["mobile"])
    return {"opted_out": len(customers), "customer_ids": [c["customer_id"] for c in customers]}


# Failures that retrying won't fix, matched in sms_history.error_message.
PERMANENT_FAILURES = (
    ("Invalid 'To' Phone Number", "geçersiz numara"),
    ("Permission to send an SMS has not been enabled", "bu bölgeye SMS izni yok"),
    ("Twilio error 30006", "sabit hat, SMS alamıyor"),
    ("Twilio error 30005", "numara kullanılmıyor veya bilinmiyor"),
)


def undeliverable_reason(last_error: str | None, failed_of_last3: int) -> str | None:
    for needle, reason in PERMANENT_FAILURES:
        if needle in (last_error or ""):
            return reason
    if failed_of_last3 >= 3:
        return "son 3 SMS teslim edilemedi"
    return None


def mark_undeliverable(*, dry_run: bool = False) -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            """WITH ranked AS (
                 SELECT customer_id, status, error_message,
                        ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY sent_at::timestamptz DESC) AS rn
                 FROM sms_history WHERE customer_id IS NOT NULL),
               recent AS (
                 SELECT customer_id,
                        MAX(error_message) FILTER (WHERE rn = 1) AS last_error,
                        BOOL_OR(rn = 1 AND status IN ('failed', 'undelivered')) AS last_failed,
                        COUNT(*) FILTER (WHERE rn <= 3 AND status IN ('failed', 'undelivered')) AS failed_of_last3
                 FROM ranked GROUP BY customer_id)
               SELECT c.customer_id, r.last_error, r.failed_of_last3
               FROM recent r JOIN customers c USING (customer_id)
               WHERE r.last_failed AND c.sms_undeliverable IS NULL AND COALESCE(c.sms_opt_out, 0) <> 1"""
        ).fetchall()
        marked: dict[str, int] = {}
        for row in rows:
            reason = undeliverable_reason(row["last_error"], row["failed_of_last3"])
            if not reason:
                continue
            marked[reason] = marked.get(reason, 0) + 1
            if not dry_run:
                conn.execute("UPDATE customers SET sms_undeliverable=%s WHERE customer_id=%s",
                             (reason, row["customer_id"]))
        conn.commit()
        return {"marked": sum(marked.values()), "reasons": marked}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    parser.add_argument("--days", type=int, default=LOOKBACK_DAYS,
                        help=f"refresh statuses of messages sent in the last N days (default {LOOKBACK_DAYS})")
    args = parser.parse_args()

    from twilio.rest import Client

    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    summary = {
        "dry_run": args.dry_run,
        "status": refresh_statuses(client, os.environ["TWILIO_PHONE_NUMBER"], days=args.days, dry_run=args.dry_run),
        "stop": sync_stop_opt_outs(ZeyDataStore(), dry_run=args.dry_run),
        "undeliverable": mark_undeliverable(dry_run=args.dry_run),
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
