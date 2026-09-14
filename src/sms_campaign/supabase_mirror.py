"""Best-effort mirror of the local SQLite store to Supabase PostgREST.

SQLite is deliberately the first durable write. This module only mirrors
already-committed rows, so a Supabase outage cannot lose Vagaro or campaign
history. The server-only ``SUPABASE_SECRET_KEY`` is never included in errors.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd


TABLES: tuple[tuple[str, str], ...] = (
    ("customers", "customer_id"),
    ("services", "service_id"),
    ("employees", "employee_id"),
    ("transactions", "transaction_id"),
    ("sms_history", "sms_id"),
    ("email_history", "email_id"),
    ("campaigns", "campaign_id"),
    ("sync_log", "log_id"),
    ("webhook_events", "event_id"),
)


@dataclass
class MirrorResult:
    status: str = "ok"
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class SupabaseMirror:
    """Upload SQLite rows through the Supabase REST interface."""

    def __init__(self, url: str, secret_key: str, batch_size: int = 250):
        self.url = url.rstrip("/")
        self.secret_key = secret_key
        self.batch_size = batch_size

    @classmethod
    def from_environment(cls) -> "SupabaseMirror | None":
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SECRET_KEY")
        if not url or not key:
            return None
        return cls(url, key)

    def mirror_table(self, table: str, primary_key: str, frame: pd.DataFrame) -> int:
        records = [_json_safe(record) for record in frame.to_dict(orient="records")]
        for start in range(0, len(records), self.batch_size):
            self._upsert(table, primary_key, records[start:start + self.batch_size])
        return len(records)

    def mirror_store(self, store: Any) -> MirrorResult:
        result = MirrorResult()
        for table, primary_key in TABLES:
            try:
                frame = store.export_table(table)
                count = self.mirror_table(table, primary_key, frame)
                result.tables[table] = {"rows": count, "status": "ok"}
            except Exception as exc:  # keep later tables and SQLite independent
                message = f"{table}: {exc}"
                result.errors.append(message)
                result.tables[table] = {"rows": 0, "status": "error", "error": str(exc)}
        result.status = "error" if result.errors else "ok"
        return result

    def _upsert(self, table: str, primary_key: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        path = urllib.parse.quote(table, safe="")
        conflict = urllib.parse.quote(primary_key, safe="")
        request = urllib.request.Request(
            f"{self.url}/rest/v1/{path}?on_conflict={conflict}",
            data=json.dumps(records, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "apikey": self.secret_key,
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30):
                return
        except urllib.error.HTTPError as exc:
            # Do not include the body: PostgREST errors can echo submitted
            # values, including customer data.
            raise RuntimeError(f"Supabase upsert failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Supabase connection failed") from exc


def _json_safe(value: Any) -> dict[str, Any]:
    """Convert pandas/numpy/date scalar values into JSON-safe values."""
    clean: dict[str, Any] = {}
    for key, item in value.items():
        if item is None:
            clean[key] = None
        elif isinstance(item, float) and pd.isna(item):
            clean[key] = None
        elif hasattr(item, "item"):
            clean[key] = item.item()
        elif isinstance(item, (datetime, date)):
            clean[key] = item.isoformat()
        else:
            clean[key] = item
    return clean
