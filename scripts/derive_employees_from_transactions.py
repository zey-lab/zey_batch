#!/usr/bin/env python3
"""Backfill employee activity identities from preserved Vagaro transactions."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from sms_campaign.data_store import ZeyDataStore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "customer_master.sqlite3"


def employee_rows(store: ZeyDataStore) -> list[dict[str, str]]:
    frame = store.export_table("transactions")
    by_id: dict[str, dict[str, str]] = {}
    for raw in frame.get("raw_json", []):
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        employee_id = str(
            payload.get("ServiceProviderID")
            or payload.get("CheckedOutByID")
            or ""
        ).strip()
        name = str(
            payload.get("ServiceProviderName")
            or payload.get("CheckedOutBy")
            or ""
        ).strip()
        if not employee_id or employee_id in {"0", "None", "nan"} or not name or name == "--":
            continue
        by_id.setdefault(employee_id, {"EmployeeID": employee_id, "Name": name})
    return list(by_id.values())


def main() -> None:
    store = ZeyDataStore(DB_PATH)
    rows = employee_rows(store)
    if not rows:
        print(json.dumps({"status": "ok", "derived": 0, "inserted": 0, "updated": 0}))
        return
    result = store.sync_employees(pd.DataFrame(rows))
    store.log_sync(
        source="vagaro_employee_from_transactions",
        fetched=len(rows),
        inserted=result.inserted,
        updated=result.updated,
        deactivated=0,
    )
    print(json.dumps({
        "status": "ok",
        "derived": len(rows),
        "inserted": result.inserted,
        "updated": result.updated,
    }))


if __name__ == "__main__":
    main()
