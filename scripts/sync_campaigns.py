#!/usr/bin/env python3
"""Import the existing campaign configuration into SQLite before mirroring."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from sms_campaign.data_store import ZeyDataStore


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "customer_master.sqlite3"
DEFAULT_CAMPAIGN_PATH = ROOT / "data" / "campaigns.xlsx"


def campaign_path() -> Path:
    """Return the configured campaign source without exposing its contents."""
    configured = os.getenv("CAMPAIGN_CONFIG_PATH")
    return Path(configured) if configured else DEFAULT_CAMPAIGN_PATH


def load_campaign_file(path: Path) -> pd.DataFrame:
    """Load an Excel or CSV campaign configuration."""
    if not path.exists():
        raise FileNotFoundError(
            f"Campaign source not found: {path}. "
            "Provide data/campaigns.xlsx (or set CAMPAIGN_CONFIG_PATH)."
        )
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".xls", ".xlsx"}:
        return pd.read_excel(path, engine="openpyxl")
    raise ValueError(f"Unsupported campaign source format: {path.suffix}")


def sync_campaign_file(path: Path, database_path: Path = DATABASE_PATH) -> dict:
    """Import campaign definitions idempotently and return a JSON-safe report."""
    frame = load_campaign_file(path)
    if frame.empty:
        return {"status": "ok", "source": str(path), "rows": 0, "inserted": 0, "updated": 0}

    result = ZeyDataStore(database_path).import_campaigns_from_dataframe(frame)
    return {
        "status": "ok",
        "source": str(path),
        "rows": len(frame),
        "inserted": result.inserted,
        "updated": result.updated,
    }


def main() -> int:
    path = campaign_path()
    if not path.exists():
        # Campaigns are optional for the Vagaro data sync. Keep the customer and
        # transaction pipeline running, but make the missing source explicit.
        print(json.dumps({"status": "skipped", "source": str(path), "reason": "campaign source not found"}))
        return 0

    try:
        print(json.dumps(sync_campaign_file(path)))
    except (ValueError, OSError) as error:
        print(json.dumps({"status": "error", "source": str(path), "error": str(error)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
