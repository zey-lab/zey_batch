"""Application-owned SQLite customer master for campaign synchronization."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from sms_campaign.models.customer import CustomerDataMerger


@dataclass(frozen=True)
class CustomerSyncResult:
    """Sanitized counts from one complete customer-export synchronization."""

    inserted: int
    updated: int
    deactivated: int
    invalid: int


class CustomerStore:
    """Persist Vagaro customer data while retaining application-owned SMS state."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS customers (
                    phone TEXT PRIMARY KEY,
                    first_name TEXT,
                    last_name TEXT,
                    last_visited TEXT,
                    birthdate TEXT,
                    customer_since TEXT,
                    source_data TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_synced_at TEXT NOT NULL,
                    active_in_latest_export INTEGER NOT NULL DEFAULT 1,
                    last_sms_sent_date TEXT,
                    last_sms_status TEXT,
                    sms_opt_out TEXT NOT NULL DEFAULT 'No',
                    opt_out_date TEXT,
                    last_review_sent_date TEXT
                )
                """
            )

    @staticmethod
    def _value(row: pd.Series, column: str) -> str | None:
        value = row.get(column)
        if value is None or pd.isna(value):
            return None
        if isinstance(value, (datetime, pd.Timestamp)):
            return value.isoformat()
        return str(value)

    @classmethod
    def _source_data(cls, row: pd.Series) -> str:
        data = {str(column): cls._value(row, str(column)) for column in row.index}
        return json.dumps(data, sort_keys=True, separators=(",", ":"))

    def sync_dataframe(self, customers: pd.DataFrame) -> CustomerSyncResult:
        """Synchronize a complete customer export without overwriting local SMS state."""
        now = datetime.now(timezone.utc).isoformat()
        normalized_rows: dict[str, pd.Series] = {}
        invalid = 0

        for _, row in customers.iterrows():
            phone = CustomerDataMerger.normalize_single_phone(row.get("Mobile"))
            if phone is None:
                invalid += 1
                continue
            normalized_rows[phone] = row

        inserted = 0
        updated = 0
        with self._connect() as connection:
            for phone, row in normalized_rows.items():
                source_fields = (
                    self._value(row, "First Name"),
                    self._value(row, "Last Name"),
                    self._value(row, "Last Visited"),
                    self._value(row, "Birthdate"),
                    self._value(row, "Customer Since"),
                    self._source_data(row),
                    now,
                )
                exists = connection.execute(
                    "SELECT 1 FROM customers WHERE phone = ?", (phone,)
                ).fetchone()
                if exists:
                    connection.execute(
                        """
                        UPDATE customers
                        SET first_name = ?, last_name = ?, last_visited = ?, birthdate = ?,
                            customer_since = ?, source_data = ?, last_synced_at = ?,
                            active_in_latest_export = 1
                        WHERE phone = ?
                        """,
                        (*source_fields, phone),
                    )
                    updated += 1
                else:
                    connection.execute(
                        """
                        INSERT INTO customers (
                            phone, first_name, last_name, last_visited, birthdate,
                            customer_since, source_data, first_seen_at, last_synced_at,
                            active_in_latest_export
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (phone, *source_fields, now),
                    )
                    inserted += 1

            if normalized_rows:
                placeholders = ", ".join("?" for _ in normalized_rows)
                result = connection.execute(
                    f"""
                    UPDATE customers
                    SET active_in_latest_export = 0
                    WHERE active_in_latest_export = 1 AND phone NOT IN ({placeholders})
                    """,
                    tuple(normalized_rows),
                )
            else:
                result = connection.execute(
                    "UPDATE customers SET active_in_latest_export = 0 WHERE active_in_latest_export = 1"
                )
            deactivated = result.rowcount

        return CustomerSyncResult(
            inserted=inserted,
            updated=updated,
            deactivated=deactivated,
            invalid=invalid,
        )

    def set_messaging_state(
        self,
        phone: str,
        *,
        last_sms_sent_date: str | None = None,
        last_sms_status: str | None = None,
        sms_opt_out: str | None = None,
        opt_out_date: str | None = None,
        last_review_sent_date: str | None = None,
    ) -> None:
        """Update only application-owned messaging state for an existing customer."""
        normalized_phone = CustomerDataMerger.normalize_single_phone(phone)
        if normalized_phone is None:
            raise ValueError("A usable mobile number is required")

        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE customers
                SET last_sms_sent_date = ?, last_sms_status = ?, sms_opt_out = ?,
                    opt_out_date = ?, last_review_sent_date = ?
                WHERE phone = ?
                """,
                (
                    last_sms_sent_date,
                    last_sms_status,
                    sms_opt_out,
                    opt_out_date,
                    last_review_sent_date,
                    normalized_phone,
                ),
            )
            if result.rowcount != 1:
                raise KeyError("Customer does not exist in the local customer master")

    def get_customer(self, phone: str) -> dict[str, Any] | None:
        """Retrieve one customer for application use without exposing the raw export."""
        normalized_phone = CustomerDataMerger.normalize_single_phone(phone)
        if normalized_phone is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT phone, first_name, last_name, last_visited, birthdate, customer_since,
                       active_in_latest_export, last_sms_sent_date, last_sms_status,
                       sms_opt_out, opt_out_date, last_review_sent_date
                FROM customers WHERE phone = ?
                """,
                (normalized_phone,),
            ).fetchone()
        if row is None:
            return None
        customer = dict(row)
        customer["active_in_latest_export"] = bool(customer["active_in_latest_export"])
        return customer
