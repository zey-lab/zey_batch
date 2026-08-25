#!/usr/bin/env python3
"""Sync Vagaro export JSON into the SQLite database."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from sms_campaign.data_store import ZeyDataStore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "customer_master.sqlite3"
EXPORT_PATH = ROOT / "data" / "incoming" / "vagaro_customers_latest.json"


def main() -> None:
    store = ZeyDataStore(DB_PATH)

    if not EXPORT_PATH.exists():
        print(json.dumps({"status": "error", "message": "No Vagaro export file found."}))
        return

    payload = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    if not rows:
        print(json.dumps({"status": "error", "message": "Export file is empty."}))
        return

    df = pd.DataFrame(rows)
    start = time.time()

    # Sync customers
    customer_result = store.sync_customers(df)
    duration = time.time() - start

    # Log sync
    store.log_sync(
        source="vagaro_customer",
        fetched=len(df),
        inserted=customer_result.inserted,
        updated=customer_result.updated,
        deactivated=customer_result.deactivated,
        errors=json.dumps(customer_result.errors) if customer_result.errors else None,
        duration=duration,
    )

    # Get stats
    stats = store.get_stats()

    print(json.dumps({
        "status": "ok",
        "exported": len(df),
        "inserted": customer_result.inserted,
        "updated": customer_result.updated,
        "deactivated": customer_result.deactivated,
        "duration_sec": round(duration, 2),
        "db_stats": stats,
    }))


if __name__ == "__main__":
    main()
