#!/usr/bin/env python3
"""Mirror committed SQLite data to Supabase without blocking local writes."""

from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from sms_campaign.data_store import ZeyDataStore
from sms_campaign.supabase_mirror import SupabaseMirror

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "customer_master.sqlite3"


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    mirror = SupabaseMirror.from_environment()
    if mirror is None:
        print(json.dumps({
            "status": "skipped",
            "message": "SUPABASE_URL and SUPABASE_SECRET_KEY are required",
        }))
        return

    result = mirror.mirror_store(ZeyDataStore(DB_PATH))
    print(json.dumps({
        "status": result.status,
        "tables": result.tables,
        "errors": result.errors,
    }))


if __name__ == "__main__":
    main()
