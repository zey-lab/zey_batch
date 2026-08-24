#!/usr/bin/env python3
"""Import a private Vagaro Customer Report export into the owned customer DB."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from sms_campaign.customer_store import CustomerStore

ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = ROOT / "data" / "incoming" / "vagaro_customers_latest.json"
DATABASE_PATH = ROOT / "data" / "customer_master.sqlite3"

COLUMN_MAP = {
    "CellPhone": "Mobile",
    "FirstName": "First Name",
    "LastName": "Last Name",
    "LastVisited": "Last Visited",
    "BirthDate": "Birthdate",
    "CustomerSince": "Customer Since",
}


def main() -> None:
    payload = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Vagaro export did not contain customer rows")

    customers = pd.DataFrame(rows).rename(columns=COLUMN_MAP)
    missing = [column for column in ("Mobile", "First Name") if column not in customers.columns]
    if missing:
        raise ValueError(f"Vagaro export is missing required mapped columns: {', '.join(missing)}")

    result = CustomerStore(DATABASE_PATH).sync_dataframe(customers)
    print(json.dumps({
        "status": "ok",
        "exported_records": len(customers),
        "inserted": result.inserted,
        "updated": result.updated,
        "deactivated": result.deactivated,
        "invalid": result.invalid,
    }))


if __name__ == "__main__":
    main()
