#!/usr/bin/env python3
"""Mirror the owned SQLite customer master to the private Google Sheet via gws."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from sms_campaign.customer_store import CustomerStore

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "customer_master.sqlite3"
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1idBA_ifhKrRH7Pij0ZRtLFWXch7ZRlft9XS8ZP6jFF4")
GWS_PATH = os.getenv("GWS_PATH", "/opt/data/.local/bin/gws")
GWS_HOME = os.getenv("GWS_HOME", "/opt/data")
SHEET_RANGE = "Customers"
BATCH_ROWS = 200


def run_gws(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GWS_PATH, *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": GWS_HOME},
    )


def sheet_rows() -> tuple[list[str], list[list[str]]]:
    customers = CustomerStore(DATABASE_PATH).export_dataframe().fillna("")
    header = [str(column) for column in customers.columns]
    rows = [[str(value) for value in row] for row in customers.itertuples(index=False, name=None)]
    return header, rows


def mirror(dry_run: bool) -> dict[str, object]:
    header, rows = sheet_rows()
    result = {"rows": len(rows), "columns": len(header), "sheet_id": SHEET_ID}
    if dry_run:
        return {"status": "dry-run", **result}

    # Clear existing data rows (keep header in row 1)
    run_gws(
        "sheets", "spreadsheets", "values", "batchClear",
        "--params", json.dumps({"spreadsheetId": SHEET_ID}),
        "--json", json.dumps({"ranges": [f"{SHEET_RANGE}!A2:L"]}),
    )

    # Write in batches via the +append helper
    for offset in range(0, len(rows), BATCH_ROWS):
        batch = rows[offset:offset + BATCH_ROWS]
        run_gws(
            "sheets", "+append",
            "--spreadsheet", SHEET_ID,
            "--json-values", json.dumps(batch, separators=(",", ":")),
        )

    return {"status": "ok", **result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(mirror(args.dry_run)))


if __name__ == "__main__":
    main()
