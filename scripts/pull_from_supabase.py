#!/usr/bin/env python3
"""Pull customers and campaigns from Supabase into local SQLite.

Supabase is the source of truth for these two tables (edits made directly
in Supabase for customer opt-outs/details or campaign configuration must
flow back into the local database the send pipeline actually reads from).
Other tables (transactions, services, employees, sms_history, email_history,
webhook_events, sync_log) remain SQLite/webhook-authoritative and are only
ever pushed OUT to Supabase, never pulled back.

Conflict rule: per-row, whichever side has the newer `updated_at` wins. This
lets a same-day webhook-driven SQLite update survive even if Supabase has a
stale copy, while still letting a genuine Supabase edit (opt-out flip,
campaign approval) flow down.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "customer_master.sqlite3"

TABLES = {
    "customers": "customer_id",
    "campaigns": "campaign_id",
}


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set in the server environment")
    return value


def fetch_supabase_rows(table: str, supabase_url: str, api_key: str) -> list[dict]:
    # PostgREST caps responses at 1000 rows by default; page with Range
    # headers until a short page tells us we have reached the end.
    page_size = 1000
    offset = 0
    rows: list[dict] = []
    while True:
        request = urllib.request.Request(
            f"{supabase_url}/rest/v1/{table}?select=*",
            headers={
                "apikey": api_key,
                "Authorization": f"Bearer {api_key}",
                "Range-Unit": "items",
                "Range": f"{offset}-{offset + page_size - 1}",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            page = json.loads(response.read().decode("utf-8"))
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def pull_table(conn: sqlite3.Connection, table: str, key: str, rows: list[dict]) -> dict:
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    local_updated = dict(conn.execute(f"SELECT {key}, updated_at FROM {table}").fetchall())

    pulled = 0
    skipped_stale = 0
    for row in rows:
        row_key = row.get(key)
        if row_key is None:
            continue
        remote_updated = row.get("updated_at")
        local_ts = local_updated.get(row_key)
        # Pull if the local row doesn't exist yet, or Supabase's copy is
        # newer (string comparison is safe: both sides use ISO-ish text).
        if local_ts is not None and remote_updated is not None and str(remote_updated) <= str(local_ts):
            skipped_stale += 1
            continue

        present = {k: v for k, v in row.items() if k in columns}
        cols = list(present.keys())
        placeholders = ", ".join(f":{c}" for c in cols)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != key)
        conn.execute(
            f"""INSERT INTO {table} ({", ".join(cols)}) VALUES ({placeholders})
                ON CONFLICT({key}) DO UPDATE SET {updates}""",
            present,
        )
        pulled += 1

    return {"table": table, "remote_rows": len(rows), "pulled": pulled, "skipped_stale": skipped_stale}


def main() -> int:
    supabase_url = _env("SUPABASE_URL")
    api_key = _env("SUPABASE_SECRET_KEY")

    conn = sqlite3.connect(str(DATABASE_PATH))
    report = {"status": "ok", "tables": {}}
    try:
        for table, key in TABLES.items():
            try:
                rows = fetch_supabase_rows(table, supabase_url, api_key)
                report["tables"][table] = pull_table(conn, table, key, rows)
            except Exception as exc:  # noqa: BLE001
                report["status"] = "error"
                report["tables"][table] = {"table": table, "status": "error", "error": str(exc)}
        conn.commit()
    finally:
        conn.close()

    print(json.dumps(report, default=str))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
