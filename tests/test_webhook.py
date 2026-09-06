from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from sms_campaign.webhook import WebhookError, WebhookProcessor


class TestWebhookProcessor(unittest.TestCase):
    def test_transaction_is_stored_and_duplicate_is_idempotent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "zey.sqlite3"
            processor = WebhookProcessor(db_path, "secret")
            processor.store.sync_customers(
                pd.DataFrame([{"UserID": "cust-1", "Mobile": "5550000001", "FirstName": "Ana"}])
            )
            event = {
                "id": "event-1",
                "createdDate": "2026-09-06T19:00:00Z",
                "type": "transaction",
                "action": "created",
                "payload": {
                    "transactionId": "txn-1",
                    "transactionDate": "2026-09-06T18:59:00Z",
                    "purchaseType": "Service",
                    "itemSold": "Brow shaping",
                    "customerId": "cust-1",
                    "tax": 1.0,
                    "tip": 5.0,
                    "amountPaid": 26.0,
                },
            }
            body = json.dumps(event).encode()
            first = processor.process({"X-Vagaro-Verification-Token": "secret"}, body)
            second = processor.process({"X-Vagaro-Verification-Token": "secret"}, body)

            self.assertEqual(first["status"], "accepted")
            self.assertEqual(second, {"status": "duplicate", "event_id": "event-1"})

            conn = sqlite3.connect(db_path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 1)
            self.assertIsNotNone(conn.execute("SELECT customer_id FROM transactions").fetchone()[0])
            conn.close()

    def test_invalid_token_is_rejected_before_persisting(self) -> None:
        with TemporaryDirectory() as temp_dir:
            processor = WebhookProcessor(Path(temp_dir) / "zey.sqlite3", "secret")
            with self.assertRaisesRegex(WebhookError, "Invalid"):
                processor.process({"X-Vagaro-Verification-Token": "wrong"}, b"{}")

    def test_customer_event_is_retained_for_snapshot_reconciliation(self) -> None:
        with TemporaryDirectory() as temp_dir:
            processor = WebhookProcessor(Path(temp_dir) / "zey.sqlite3", "secret")
            result = processor.process(
                {"Authorization": "Bearer secret"},
                json.dumps(
                    {
                        "id": "event-customer",
                        "type": "customer",
                        "action": "updated",
                        "payload": {"customerId": "cust-1", "mobilePhone": "5550000001"},
                    }
                ).encode(),
            )
            self.assertEqual(result["customer_sync"], "deferred_to_snapshot")


if __name__ == "__main__":
    unittest.main()
