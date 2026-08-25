#!/usr/bin/env python3
"""Sync Vagaro employees export into SQLite."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from sms_campaign.data_store import ZeyDataStore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "customer_master.sqlite3"
EXPORT_PATH = ROOT / "data" / "incoming" / "vagaro_employees_latest.json"


def main() -> None:
    store = ZeyDataStore(DB_PATH)

    if not EXPORT_PATH.exists():
        print(json.dumps({"status": "error", "message": "No employees export file found."}))
        return

    payload = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    if not rows:
        print(json.dumps({"status": "error", "message": "Export file is empty."}))
        return

    df = pd.DataFrame(rows)
    start = time.time()
    result = store.sync_employees(df)
    duration = time.time() - start

    store.log_sync(
        source="vagaro_employee",
        fetched=len(df),
        inserted=result.inserted,
        updated=result.updated,
        deactivated=0,
        duration=duration,
    )

    print(json.dumps({
        "status": "ok",
        "exported": len(df),
        "inserted": result.inserted,
        "updated": result.updated,
        "duration_sec": round(duration, 2),
    }))


if __name__ == "__main__":
    main()
