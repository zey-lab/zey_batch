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

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "customers": ("customer_id", "vagaro_user_id", "mobile", "first_name", "last_name", "email", "birthdate", "gender", "address", "city", "state", "zip", "apt_suite", "customer_since", "last_visit", "membership", "referred_by", "online_booking", "tags", "communication_preference", "sms_opt_out", "opt_out_date", "email_opt_out", "active", "acquisition", "bank_name_number", "cdn_url", "country_id", "custom_fields_groups", "day_phone", "email_failed_reason", "email_format", "general_tag", "is_valid_email", "is_valid_text", "night_phone", "no_of_booking", "no_of_class_booked", "no_of_class_check_ins", "no_show_cancel", "photo", "service_providers", "street_address", "street_no", "text_failed_reason", "total_amount_paid", "total_points_accumulated", "ucc_no", "ucc_type", "enc_user_id", "raw_json", "created_at", "updated_at"),
    "services": ("service_id", "customer_id", "employee_name", "service_name", "service_date", "duration_min", "amount_paid", "notes", "vagaro_appt_id", "created_at"),
    "employees": ("employee_id", "vagaro_emp_id", "name", "role", "phone", "email", "active", "schedule_json", "created_at", "updated_at"),
    "transactions": ("transaction_id", "vagaro_transaction_id", "customer_id", "customer_name", "employee_name", "transaction_date", "transaction_type", "payment_method", "subtotal", "tax", "tip", "discount", "total_amount", "status", "notes", "raw_json", "created_at"),
    "sms_history": ("sms_id", "customer_id", "campaign_type", "message_text", "sent_at", "status", "twilio_sid", "error_message", "campaign_row", "created_at"),
    "email_history": ("email_id", "customer_id", "campaign_type", "subject", "body", "sent_at", "status", "error_message", "created_at"),
    "campaigns": ("campaign_id", "text_prompt", "character_limit", "campaign_type", "filter_last_visit_days", "filter_last_sms_days", "rank", "process_date", "process_status", "active", "created_at", "updated_at"),
    "sync_log": ("log_id", "sync_date", "source", "records_fetched", "records_inserted", "records_updated", "records_deactivated", "errors", "duration_sec"),
    "webhook_events": ("event_id", "event_type", "action", "event_created_at", "payload_json", "received_at", "processed_at", "process_status", "error_message"),
}

INTEGER_COLUMNS: dict[str, frozenset[str]] = {
    "customers": frozenset({"customer_id", "sms_opt_out", "email_opt_out", "active", "is_valid_email", "is_valid_text", "no_of_booking", "no_of_class_booked", "no_of_class_check_ins", "no_show_cancel"}),
    "services": frozenset({"service_id", "customer_id", "duration_min"}),
    "employees": frozenset({"employee_id", "active"}),
    "transactions": frozenset({"transaction_id", "customer_id"}),
    "sms_history": frozenset({"sms_id", "customer_id", "campaign_row"}),
    "email_history": frozenset({"email_id", "customer_id"}),
    "campaigns": frozenset({"campaign_id", "character_limit", "filter_last_visit_days", "filter_last_sms_days", "rank", "active"}),
    "sync_log": frozenset({"log_id", "records_fetched", "records_inserted", "records_updated", "records_deactivated"}),
}


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
        allowed = TABLE_COLUMNS.get(table)
        integer_columns = INTEGER_COLUMNS.get(table, frozenset())
        records = [
            _json_safe(record, allowed=allowed, integer_columns=integer_columns)
            for record in frame.to_dict(orient="records")
        ]
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


def _json_safe(
    value: Any,
    *,
    allowed: tuple[str, ...] | None = None,
    integer_columns: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Convert pandas/numpy/date scalar values into JSON-safe values."""
    clean: dict[str, Any] = {}
    for key, item in value.items():
        if allowed is not None and key not in allowed:
            continue
        if item is None:
            clean[key] = None
        elif isinstance(item, float) and pd.isna(item):
            clean[key] = None
        elif hasattr(item, "item"):
            item = item.item()
            clean[key] = _as_integer(key, item, integer_columns)
        elif isinstance(item, (datetime, date)):
            clean[key] = item.isoformat()
        else:
            clean[key] = _as_integer(key, item, integer_columns)
    return clean


def _as_integer(key: str, value: Any, integer_columns: frozenset[str]) -> Any:
    if key not in integer_columns or value is None:
        return value
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return value
