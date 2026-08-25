"""Mirror all SQLite tables to a multi-sheet Google Workbook via gws."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from sms_campaign.data_store import ZeyDataStore

ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = ROOT / "data" / "customer_master.sqlite3"
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1idBA_ifhKrRH7Pij0ZRtLFWXch7ZRlft9XS8ZP6jFF4")
GWS_PATH = os.getenv("GWS_PATH", "/opt/data/.local/bin/gws")
GWS_HOME = os.getenv("GWS_HOME", "/opt/data")

# Table → Sheet name mapping
TABLE_SHEETS = {
    "customers": "Customers",
    "sms_history": "SMS History",
    "services": "Services",
    "employees": "Employees",
    "campaigns": "Campaigns",
    "sync_log": "Sync Log",
}

BATCH_ROWS = 200


def run_gws(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GWS_PATH, *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": GWS_HOME},
    )


def mirror_table(store: ZeyDataStore, table: str, sheet_name: str) -> dict:
    """Mirror one SQLite table to one Google Sheet tab."""
    df = store.export_table(table)
    df = df.fillna("")

    if df.empty:
        return {"table": table, "sheet": sheet_name, "rows": 0}

    header = [str(c) for c in df.columns]
    rows = [[str(v) for v in row] for row in df.itertuples(index=False, name=None)]

    # Clear existing data
    run_gws(
        "sheets", "spreadsheets", "values", "batchClear",
        "--params", json.dumps({"spreadsheetId": SHEET_ID}),
        "--json", json.dumps({"ranges": [f"{sheet_name}!A2:Z"]}),
    )

    # Write header
    run_gws(
        "sheets", "+append",
        "--spreadsheet", SHEET_ID,
        "--json-values", json.dumps([header], separators=(",", ":")),
    )

    # Write data in batches
    for offset in range(0, len(rows), BATCH_ROWS):
        batch = rows[offset:offset + BATCH_ROWS]
        run_gws(
            "sheets", "+append",
            "--spreadsheet", SHEET_ID,
            "--json-values", json.dumps(batch, separators=(",", ":")),
        )

    return {"table": table, "sheet": sheet_name, "rows": len(rows)}


def mirror_all(store: ZeyDataStore, dry_run: bool = False) -> dict:
    """Mirror all tables to Google Sheets."""
    results = {}
    for table, sheet_name in TABLE_SHEETS.items():
        if dry_run:
            df = store.export_table(table)
            results[table] = {"sheet": sheet_name, "rows": len(df), "status": "dry-run"}
        else:
            results[table] = mirror_table(store, table, sheet_name)
    return {"status": "ok" if not dry_run else "dry-run", "sheets": results}
