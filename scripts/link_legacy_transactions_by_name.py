#!/usr/bin/env python3
"""One-off (2026-10-07, approved by the owner): link legacy report-imported
transactions to customers by name.

These rows came from the 2026-09-06 Vagaro report import and reference a
numeric Vagaro customer id that isn't on file -- most belong to a second
Vagaro profile of a customer we already have under another id. A row is
linked only when its "First Last" name matches exactly one customer
(case-insensitive) and every row of the same Vagaro id agrees on that
customer. The linked ids are written to data/backups/ first. Dry run by
default; pass --apply to commit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sms_campaign.db import get_connection  # noqa: E402

VAGARO_ID = re.compile(r'"CustomerID":\s*(\d+)')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="commit the changes (default: dry run)")
    args = parser.parse_args()

    conn = get_connection()
    try:
        by_name: dict[tuple[str, str], list[int]] = defaultdict(list)
        for c in conn.execute("SELECT customer_id, first_name, last_name FROM customers").fetchall():
            key = ((c["first_name"] or "").strip().lower(), (c["last_name"] or "").strip().lower())
            by_name[key].append(c["customer_id"])

        rows = conn.execute(
            "SELECT transaction_id, customer_name, raw_json FROM transactions "
            "WHERE customer_id IS NULL AND customer_name IS NOT NULL"
        ).fetchall()
        candidates: dict[str, list[tuple[int, int | None]]] = defaultdict(list)
        for r in rows:
            match = VAGARO_ID.search(r["raw_json"] or "")
            if not match:
                continue
            first, _, last = r["customer_name"].strip().partition(" ")
            ids = by_name.get((first.lower(), last.strip().lower()), [])
            candidates[match.group(1)].append((r["transaction_id"], ids[0] if len(ids) == 1 else None))

        links: list[tuple[int, int]] = []
        skipped = {"no_unique_name_match": 0, "vagaro_id_disagrees": 0}
        for txns in candidates.values():
            targets = {cid for _, cid in txns}
            if None in targets:
                skipped["no_unique_name_match"] += len(txns)
            elif len(targets) > 1:
                skipped["vagaro_id_disagrees"] += len(txns)
            else:
                links.extend((tid, cid) for tid, cid in txns)

        summary = {
            "legacy_rows": sum(len(t) for t in candidates.values()),
            "to_link": len(links),
            "customers": len({cid for _, cid in links}),
            "skipped": skipped,
        }
        if not args.apply:
            print(json.dumps({"dry_run": True, **summary}))
            return

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = ROOT / "data" / "backups" / f"link_legacy_transactions_{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps([{"transaction_id": t, "customer_id": c} for t, c in links]))
        updated = 0
        for tid, cid in links:
            updated += conn.execute(
                "UPDATE transactions SET customer_id=%s WHERE transaction_id=%s AND customer_id IS NULL",
                (cid, tid),
            ).rowcount
        conn.commit()
        print(json.dumps({"applied": True, **summary, "updated": updated, "backup": str(backup)}))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
