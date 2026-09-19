"""Vagaro webhook authentication, durable receipt, and light ingestion."""

from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from sms_campaign import vagaro_api
from sms_campaign.data_store import ZeyDataStore
from sms_campaign.db import get_connection

logger = logging.getLogger(__name__)

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

        conn = get_connection()
        try:
            inserted = conn.execute(
                """INSERT INTO webhook_events
                   (event_id, event_type, action, event_created_at, payload_json)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (event_id) DO NOTHING""",
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
            derived = self._ingest(event_type, payload, self._text(event.get("action")))
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
            "X-Vagaro-Signature",
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

    def _ensure_customer_known(self, vagaro_customer_id: str | None) -> None:
        """Best-effort: if an appointment/transaction references a customer
        we've never seen a 'customer' webhook for, fetch it from Vagaro's
        API and create the record now instead of leaving the reference
        permanently unlinked (found 2026-09-16: Vagaro doesn't reliably
        send a companion 'customer' webhook when a walk-in books/pays).
        Never raises -- a Vagaro API hiccup must not block ingesting the
        appointment/transaction itself.
        """
        if not vagaro_customer_id:
            return
        if self.store.find_customer_id_by_vagaro_ref(vagaro_customer_id) is not None:
            return
        try:
            data = vagaro_api.fetch_customer(vagaro_customer_id)
        except Exception:
            logger.exception("Vagaro API customer lookup failed for %s", vagaro_customer_id)
            return
        if data:
            self.store.sync_customer_from_webhook(data, "created")

    def _ingest(self, event_type: str, payload: dict, action: str = "") -> dict[str, object]:
        if event_type == "transaction":
            self._ensure_customer_known(payload.get("customerId"))
            # Vagaro transaction webhooks are compact and do not reliably
            # include customer/staff names or the complete report fields.
            # Retain the raw event above, but do not expose a partial event as
            # a canonical transaction row in the workbook.
            if not self._has_complete_transaction(payload):
                return {
                    "derived_table": "webhook_events",
                    "transaction_sync": "deferred_to_complete_snapshot",
                }

            total = payload.get("totalAmount", payload.get("amountPaid", self._payment_total(payload)))
            tax = payload.get("tax") or 0
            tip = payload.get("tip") or 0
            discount = payload.get("discount") or 0
            subtotal = payload.get("subtotal")
            if subtotal is None and total is not None:
                # Vagaro's real payload never sends subtotal directly, only
                # the itemized payment total (which includes tip) plus tax/
                # tip/discount as separate fields -- back it out the normal
                # invoice way: total = subtotal + tax + tip - discount.
                subtotal = total - tax - tip + discount

            row = {
                # Vagaro's transactionId identifies the whole checkout and
                # repeats across every line item when a checkout sells more
                # than one service -- using it as our uniqueness key
                # silently overwrote one line item with another (found
                # 2026-09-16: 30/83 checkouts had 2+ line items, 40 earlier
                # transactions lost this way). userPaymentId is unique per
                # line item.
                "ID": payload.get("userPaymentId") or payload.get("transactionId"),
                "TransactionDate": self._fix_mislabeled_central_timestamp(payload.get("transactionDate")),
                "TransactionType": payload.get("purchaseType"),
                "PaymentMethod": payload.get("ccType") or payload.get("paymentMethod"),
                "SubTotal": subtotal,
                "Tax": tax,
                "Tip": tip,
                "Discount": discount,
                "Total": total,
                "CustomerID": payload.get("customerId"),
                "Employee": payload.get("serviceProviderId"),
                "CustomerName": payload.get("customerName"),
                "Notes": payload.get("itemSold"),
            }
            result = self.store.sync_transactions(pd.DataFrame([row]))
            return {"derived_table": "transactions", "inserted": result.inserted, "updated": result.updated}

        if event_type == "appointment":
            self._ensure_customer_known(payload.get("customerId"))
            row = {
                "AppointmentID": payload.get("appointmentId"),
                "CustomerID": payload.get("customerId"),
                "Employee": payload.get("serviceProviderId"),
                "Service": payload.get("serviceTitle"),
                "Date": payload.get("startTime"),
                "Amount": payload.get("amount"),
                "Duration": self._minutes_between(payload.get("startTime"), payload.get("endTime")),
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

        if event_type == "customer":
            # Scoped to exactly this one customer_id -- cannot affect any
            # other row, unlike the bulk report import this replaces.
            result = self.store.sync_customer_from_webhook(payload, action)
            return {"derived_table": "customers", **result}

        return {"derived_table": "webhook_events", "status": "unsupported_event_type"}

    @classmethod
    def _has_complete_transaction(cls, payload: dict) -> bool:
        """Return whether a webhook has enough detail for the canonical table.

        Vagaro's real transaction webhook never includes customerName or a
        single totalAmount/amountPaid field -- it sends itemized payment
        components instead (ccAmount, cashAmount, etc.). Requiring those
        two fields silently deferred every single real transaction webhook
        forever (found 2026-09-15: 103/103 received, 0 ever reached the
        transactions table). customerName is dropped from the requirement;
        the total is computed from the itemized components instead.
        """
        required_text = ("transactionId", "transactionDate", "customerId", "itemSold")
        return all(cls._text(payload.get(key)) for key in required_text)

    @staticmethod
    def _fix_mislabeled_central_timestamp(value: object) -> str | None:
        """Vagaro's transactionDate carries a 'Z' (UTC) suffix but the
        value is actually America/Chicago local time (confirmed 2026-09-15:
        every transactionDate sits exactly 5h -- the CDT offset -- behind
        the webhook's own createdDate, which IS genuine UTC). Re-interpret
        as Chicago local time and convert properly so it lines up with
        received_at/created_at, and so the correction stays right across
        DST transitions instead of a hardcoded -5h."""
        if not value:
            return None
        try:
            naive = datetime.fromisoformat(str(value).replace("Z", ""))
        except ValueError:
            return str(value)
        local = naive.replace(tzinfo=ZoneInfo("America/Chicago"))
        return local.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _payment_total(payload: dict) -> float | None:
        """Sum Vagaro's itemized payment-method fields into one total
        (excludes tip, which the schema tracks as its own column)."""
        components = (
            "ccAmount", "cashAmount", "checkAmount", "achAmount",
            "bankAccountAmount", "vagaroPayLaterAmount", "otherAmount",
            "packageRedemption", "gcRedemption", "memberShipAmount",
        )
        values = [payload.get(key) for key in components if payload.get(key) is not None]
        if not values:
            return None
        return sum(float(v) for v in values)

    def _mark(self, event_id: str, status: str, error: str | None) -> None:
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE webhook_events
                   SET processed_at=%s, process_status=%s, error_message=%s
                   WHERE event_id=%s""",
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
    def _minutes_between(start: object, end: object) -> int | None:
        if not start or not end:
            return None
        try:
            start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        except ValueError:
            return None
        return round((end_dt - start_dt).total_seconds() / 60)

    @staticmethod
    def _text(value: object) -> str:
        return str(value).strip() if value is not None else ""
