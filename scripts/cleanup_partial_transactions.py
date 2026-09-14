#!/usr/bin/env python3
"""Remove incomplete webhook-derived rows from canonical transactions.

The webhook receiver retains those compact events verbatim in
``webhook_events``. They must not be presented as complete report rows.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "customer_master.sqlite3"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(os.getenv("ZEY_DATABASE_PATH", DEFAULT_DB)))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(args.database)
    try:
        where = (
            "json_extract(raw_json, '$.TransactionType') IS NOT NULL "
            "AND json_extract(raw_json, '$.TranType') IS NULL"
        )
        identified = conn.execute(f"SELECT COUNT(*) FROM transactions WHERE {where}").fetchone()[0]
        if not args.dry_run:
            conn.execute(f"DELETE FROM transactions WHERE {where}")
            conn.commit()
        remaining = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    finally:
        conn.close()

    print({"identified": identified, "removed": 0 if args.dry_run else identified, "remaining": remaining})


if __name__ == "__main__":
    main()
