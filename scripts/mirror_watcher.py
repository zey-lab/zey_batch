#!/usr/bin/env python3
"""Mirror committed SQLite changes to Google Sheets with debounce.

This is a lightweight fallback for hosts where the webhook receiver is owned
by another service account. It never writes to SQLite and coalesces bursts of
webhook events into one workbook refresh.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = Path(os.getenv("ZEY_DATABASE_PATH", str(ROOT / "data" / "customer_master.sqlite3")))
MIRROR_SCRIPT = ROOT / "scripts" / "mirror_to_sheets.py"
POLL_SECONDS = float(os.getenv("SHEET_MIRROR_POLL_SECONDS", "5"))
DEBOUNCE_SECONDS = float(os.getenv("SHEET_MIRROR_DEBOUNCE_SECONDS", "30"))

ROOT.joinpath("logs").mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s mirror_watcher: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "logs" / "sheet-mirror-watcher.log"),
    ],
)
logger = logging.getLogger(__name__)


def data_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA data_version").fetchone()[0])


def mirror() -> None:
    subprocess.run(
        ["uv", "run", "python", str(MIRROR_SCRIPT)],
        cwd=ROOT,
        env={**os.environ, "GWS_HOME": os.getenv("GWS_HOME", "/opt/data")},
        check=True,
        timeout=600,
    )


def main() -> None:
    conn = sqlite3.connect(str(DATABASE_PATH))
    try:
        observed = data_version(conn)
        dirty_since: float | None = None
        while True:
            time.sleep(POLL_SECONDS)
            current = data_version(conn)
            if current != observed:
                observed = current
                dirty_since = dirty_since or time.monotonic()

            if dirty_since is None or time.monotonic() - dirty_since < DEBOUNCE_SECONDS:
                continue

            try:
                logger.info("database changed; mirroring workbook")
                mirror()
            except Exception:
                # Keep the dirty marker so a transient gws/OAuth failure is
                # retried on the next polling interval without losing state.
                logger.exception("workbook mirror failed; will retry")
                time.sleep(max(POLL_SECONDS, DEBOUNCE_SECONDS))
            else:
                dirty_since = None
    finally:
        conn.close()


if __name__ == "__main__":
    main()
