"""Mirror all SQLite tables to a multi-sheet Google Workbook via gws."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from sms_campaign.data_store import ZeyDataStore

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "customer_master.sqlite3"
SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1M8sIzteYlgKHiG44pEYIr60WwytfV5-MMc-P-6HasZQ")
GWS_PATH = os.getenv("GWS_PATH", "/opt/data/.local/bin/gws")
GWS_HOME = os.getenv("GWS_HOME", "/opt/data")

# Table → Sheet name mapping
TABLE_SHEETS = {
    "customers": "Customers",
    "sms_history": "SMS History",
    "services": "Services",
    "transactions": "Transactions",
    "employees": "Employees",
    "campaigns": "Campaigns",
    "sync_log": "Sync Log",
}

BATCH_ROWS = 200
# Keep room for the process environment and gws arguments on small ARG_MAX
# environments; transaction rows include a large raw_json column.
MAX_JSON_ARG_BYTES = 8_000


def _value_batches(rows: list[list[str]]) -> list[list[list[str]]]:
    """Split rows by both count and argv size for the gws JSON argument."""
    batches = []
    batch = []
    for row in rows:
        candidate = batch + [row]
        candidate_size = len(json.dumps({"values": candidate}).encode("utf-8"))
        if batch and (len(candidate) > BATCH_ROWS or candidate_size > MAX_JSON_ARG_BYTES):
            batches.append(batch)
            batch = [row]
        else:
            batch = candidate
    if batch:
        batches.append(batch)
    return batches


def run_gws(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GWS_PATH, *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": GWS_HOME},
    )


def widen_grid(sheet_gid: int, column_count: int = 60) -> None:
    """Ensure a tab's grid has at least `column_count` columns."""
    run_gws(
        "sheets", "spreadsheets", "batchUpdate",
        "--params", json.dumps({"spreadsheetId": SHEET_ID}),
        "--json", json.dumps({
            "requests": [{
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_gid,
                        "gridProperties": {"columnCount": column_count},
                    },
                    "fields": "gridProperties.columnCount",
                },
            }],
        }),
    )


def get_sheet_ids() -> dict[str, int]:
    """Map sheet tab name -> sheetId (gid) via spreadsheets.get."""
    proc = run_gws(
        "sheets", "spreadsheets", "get",
        "--params", json.dumps({"spreadsheetId": SHEET_ID}),
    )
    meta = json.loads(proc.stdout)
    return {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta.get("sheets", [])
    }


def _col_letter(n: int) -> str:
    """1-indexed column number -> A1 column letter(s)."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def mirror_table(store: ZeyDataStore, table: str, sheet_name: str, sheet_gid: int | None = None) -> dict:
    """Mirror one SQLite table to one Google Sheet tab."""
    df = store.export_table(table)
    df = df.fillna("")

    if df.empty:
        return {"table": table, "sheet": sheet_name, "rows": 0}

    header = [str(c) for c in df.columns]
    rows = [[str(v) for v in row] for row in df.itertuples(index=False, name=None)]
    n_cols = max(len(header), 26)
    last_col = _col_letter(max(n_cols, 78))  # BZ = 78

    if sheet_gid is not None:
        widen_grid(sheet_gid, max(n_cols + 5, 60))

    # Clear existing data across the full width
    run_gws(
        "sheets", "spreadsheets", "values", "batchClear",
        "--params", json.dumps({"spreadsheetId": SHEET_ID}),
        "--json", json.dumps({"ranges": [f"{sheet_name}!A2:{last_col}"]}),
    )

    # Write header at a fixed range (not append — append skips rows with
    # stale trailing-column values, landing the header far below row 2)
    run_gws(
        "sheets", "spreadsheets", "values", "update",
        "--params", json.dumps({
            "spreadsheetId": SHEET_ID,
            "range": f"{sheet_name}!A1",
            "valueInputOption": "RAW",
        }),
        "--json", json.dumps({"values": [header]}),
    )

    # Write data in batches at fixed, sequential ranges
    start_row = 2
    for batch in _value_batches(rows):
        run_gws(
            "sheets", "spreadsheets", "values", "update",
            "--params", json.dumps({
                "spreadsheetId": SHEET_ID,
                "range": f"{sheet_name}!A{start_row}",
                "valueInputOption": "RAW",
            }),
            "--json", json.dumps({"values": batch}),
        )
        start_row += len(batch)

    return {"table": table, "sheet": sheet_name, "rows": len(rows)}


def mirror_all(store: ZeyDataStore, dry_run: bool = False) -> dict:
    """Mirror all tables to Google Sheets."""
    results = {}
    sheet_ids = {} if dry_run else get_sheet_ids()
    for table, sheet_name in TABLE_SHEETS.items():
        if dry_run:
            df = store.export_table(table)
            results[table] = {"sheet": sheet_name, "rows": len(df), "status": "dry-run"}
        else:
            results[table] = mirror_table(store, table, sheet_name, sheet_ids.get(sheet_name))
    return {"status": "ok" if not dry_run else "dry-run", "sheets": results}
