"""Vagaro webhook authentication, durable receipt, and light ingestion."""

from __future__ import annotations

import hmac
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pandas as pd

from sms_campaign.data_store import ZeyDataStore

SUPPORTED_TYPES = {"appointment", "customer", "employee", "transaction"}


class WebhookError(ValueError):
    """A webhook request is invalid or cannot be authenticated."""


class WebhookProcessor:
    """Process Vagaro events without losing the original payload."""

    def __init__(self, db_path: Path, verification_token: str | None = None):
        self.db_path = db_path
        self.verification_token = verification_token or ""
        self.store = ZeyDataStore(db_path)

    def process(self, headers: Mapping[str, str], body: bytes) -> dict[str, object]:
        self._authenticate(headers)
        try:
            event = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebhookError("Request body must be valid UTF-8 JSON") from exc

        if not isinstance(event, dict):
            raise WebhookError("Webhook body must be a JSON object")

        event_id = self._text(event.get("id"))
        event_type = self._text(event.get("type"))
        if not event_id:
            raise WebhookError("Webhook event is missing id")
        if event_type not in SUPPORTED_TYPES:
            raise WebhookError(f"Unsupported webhook event type: {event_type or 'missing'}")

        payload = event.get("payload")
        if not isinstance(payload, dict):
            raise WebhookError("Webhook event is missing payload object")

        conn = sqlite3.connect(str(self.db_path))
        try:
            inserted = conn.execute(
                """INSERT OR IGNORE INTO webhook_events
                   (event_id, event_type, action, event_created_at, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    event_id,
                    event_type,
                    self._text(event.get("action")),
                    self._text(event.get("createdDate", event.get("createdAt"))),
                    json.dumps(event, separators=(",", ":"), ensure_ascii=False),
                ),
            ).rowcount
            conn.commit()
        finally:
            conn.close()

        if not inserted:
            return {"status": "duplicate", "event_id": event_id}

        try:
            derived = self._ingest(event_type, payload)
            self._mark(event_id, "processed", None)
        except Exception as exc:  # retain receipt when a mapping needs repair
            self._mark(event_id, "error", str(exc))
            raise

        return {"status": "accepted", "event_id": event_id, **derived}

    def _authenticate(self, headers: Mapping[str, str]) -> None:
        if not self.verification_token:
            raise WebhookError("Webhook verification token is not configured")

        supplied = ""
        for name in (
            "X-Vagaro-Verification-Token",
            "X-Verification-Token",
            "X-Webhook-Verification-Token",
        ):
            supplied = self._header(headers, name)
            if supplied:
                break
        if not supplied:
            authorization = self._header(headers, "Authorization")
            if authorization.lower().startswith("bearer "):
                supplied = authorization[7:].strip()

        if not hmac.compare_digest(supplied, self.verification_token):
            raise WebhookError("Invalid webhook verification token")

    def _ingest(self, event_type: str, payload: dict) -> dict[str, object]:
        if event_type == "transaction":
            row = {
                "ID": payload.get("transactionId"),
                "TransactionDate": payload.get("transactionDate"),
                "TransactionType": payload.get("purchaseType"),
                "PaymentMethod": payload.get("ccType") or payload.get("paymentMethod"),
                "SubTotal": payload.get("subtotal"),
                "Tax": payload.get("tax"),
                "Tip": payload.get("tip"),
                "Discount": payload.get("discount"),
                "Total": payload.get("totalAmount", payload.get("amountPaid")),
                "CustomerID": payload.get("customerId"),
                "Employee": payload.get("serviceProviderId"),
                "CustomerName": payload.get("customerName"),
                "Notes": payload.get("itemSold"),
            }
            result = self.store.sync_transactions(pd.DataFrame([row]))
            return {"derived_table": "transactions", "inserted": result.inserted, "updated": result.updated}

        if event_type == "appointment":
            row = {
                "AppointmentID": payload.get("appointmentId"),
                "CustomerID": payload.get("customerId"),
                "Employee": payload.get("serviceProviderId"),
                "Service": payload.get("serviceTitle"),
                "Date": payload.get("startTime"),
                "Amount": payload.get("amount"),
            }
            result = self.store.sync_services(pd.DataFrame([row]))
            return {"derived_table": "services", "inserted": result.inserted, "updated": result.updated}

        if event_type == "employee":
            first = payload.get("employeeFirstName") or ""
            last = payload.get("employeeLastName") or ""
            row = {
                "EmployeeID": payload.get("serviceProviderId"),
                "Name": " ".join(part for part in (first, last) if part),
                "Role": payload.get("employeeType"),
                "Phone": payload.get("mobilePhone"),
                "Email": payload.get("email"),
                "Active": payload.get("isActive", True),
            }
            result = self.store.sync_employees(pd.DataFrame([row]))
            return {"derived_table": "employees", "inserted": result.inserted, "updated": result.updated}

        # A customer webhook is retained verbatim and reconciled by the daily
        # customer snapshot. A one-row event must not deactivate other customers.
        return {"derived_table": "webhook_events", "customer_sync": "deferred_to_snapshot"}

    def _mark(self, event_id: str, status: str, error: str | None) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """UPDATE webhook_events
                   SET processed_at=?, process_status=?, error_message=?
                   WHERE event_id=?""",
                (datetime.now(timezone.utc).isoformat(), status, error, event_id),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _header(headers: Mapping[str, str], wanted: str) -> str:
        wanted = wanted.lower()
        for name, value in headers.items():
            if str(name).lower() == wanted:
                return str(value).strip()
        return ""

    @staticmethod
    def _text(value: object) -> str:
        return str(value).strip() if value is not None else ""
