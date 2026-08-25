"""Mirror all SQLite tables to a multi-sheet Google Workbook via gws."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from sms_campaign.data_store import ZeyDataStore

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s mirror_to_sheets: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

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
    "employees": "Employees",
    "campaigns": "Campaigns",
    "sync_log": "Sync Log",
}

# Batch size for values.update calls. gws receives the JSON payload as a CLI
# argument, so a batch must stay well under the OS ARG_MAX (~2MB, shared with
# the process environment) even for wide tables — 200 rows of the widest
# table (Customers, 53 columns) blew past that limit ("Argument list too
# long"). 40 keeps every table's batches safely under the limit.
BATCH_ROWS = 40


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
    for offset in range(0, len(rows), BATCH_ROWS):
        batch = rows[offset:offset + BATCH_ROWS]
        start_row = offset + 2  # row 1 is the header
        run_gws(
            "sheets", "spreadsheets", "values", "update",
            "--params", json.dumps({
                "spreadsheetId": SHEET_ID,
                "range": f"{sheet_name}!A{start_row}",
                "valueInputOption": "RAW",
            }),
            "--json", json.dumps({"values": batch}),
        )

    return {"table": table, "sheet": sheet_name, "rows": len(rows)}


def mirror_all(store: ZeyDataStore, dry_run: bool = False) -> dict:
    """Mirror all tables to Google Sheets. A failure on one table is logged
    and recorded per-table; it does not abort the remaining tables."""
    results = {}
    had_error = False
    sheet_ids = {} if dry_run else get_sheet_ids()
    for table, sheet_name in TABLE_SHEETS.items():
        if dry_run:
            df = store.export_table(table)
            results[table] = {"sheet": sheet_name, "rows": len(df), "status": "dry-run"}
            continue
        try:
            results[table] = mirror_table(store, table, sheet_name, sheet_ids.get(sheet_name))
            logger.info("mirrored %s -> %s (%d rows)", table, sheet_name, results[table]["rows"])
        except Exception as exc:
            had_error = True
            logger.exception("failed to mirror %s -> %s", table, sheet_name)
            results[table] = {"table": table, "sheet": sheet_name, "error": str(exc)}
    if dry_run:
        return {"status": "dry-run", "sheets": results}
    return {"status": "error" if had_error else "ok", "sheets": results}


def main() -> None:
    if not DATABASE_PATH.exists():
        logger.error("database not found at %s", DATABASE_PATH)
        print(json.dumps({"status": "error", "message": f"Database not found: {DATABASE_PATH}"}))
        sys.exit(1)

    store = ZeyDataStore(DATABASE_PATH)
    try:
        result = mirror_all(store)
    except Exception as exc:
        logger.exception("mirror run failed")
        print(json.dumps({"status": "error", "message": str(exc)}))
        sys.exit(1)

    print(json.dumps(result))
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
