from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from sms_campaign.webhook import WebhookError, WebhookProcessor


class TestWebhookProcessor(unittest.TestCase):
    def test_real_shape_transaction_webhook_is_ingested_not_deferred(self) -> None:
        """Regression test for the 2026-09-15 incident: Vagaro's real
        transaction webhook never sends customerName or a single
        totalAmount/amountPaid field (only itemized payment components like
        ccAmount/cashAmount). The old completeness check required both,
        which silently deferred every single real transaction webhook
        forever -- 103 received, 0 ever reached the transactions table."""
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
                    "ccAmount": 25.0,
                    "cashAmount": 0,
                },
            }
            body = json.dumps(event).encode()
            first = processor.process({"X-Vagaro-Verification-Token": "secret"}, body)
            second = processor.process({"X-Vagaro-Verification-Token": "secret"}, body)

            self.assertEqual(first["status"], "accepted")
            self.assertEqual(first["derived_table"], "transactions")
            self.assertEqual(second, {"status": "duplicate", "event_id": "event-1"})

    def test_genuinely_incomplete_transaction_is_still_deferred(self) -> None:
        """A transaction webhook missing a truly required field (itemSold)
        must still defer to the canonical-table snapshot, not be guessed."""
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "zey.sqlite3"
            processor = WebhookProcessor(db_path, "secret")
            event = {
                "id": "event-2",
                "createdDate": "2026-09-06T19:00:00Z",
                "type": "transaction",
                "action": "created",
                "payload": {
                    "transactionId": "txn-2",
                    "transactionDate": "2026-09-06T18:59:00Z",
                    "customerId": "cust-1",
                },
            }
            body = json.dumps(event).encode()
            result = processor.process({"X-Vagaro-Verification-Token": "secret"}, body)

            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["transaction_sync"], "deferred_to_complete_snapshot")

            conn = sqlite3.connect(db_path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM webhook_events").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], 0)
            conn.close()

    def test_invalid_token_is_rejected_before_persisting(self) -> None:
        with TemporaryDirectory() as temp_dir:
            processor = WebhookProcessor(Path(temp_dir) / "zey.sqlite3", "secret")
            with self.assertRaisesRegex(WebhookError, "Invalid"):
                processor.process({"X-Vagaro-Verification-Token": "wrong"}, b"{}")

    def test_customer_created_webhook_inserts_a_new_customer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "zey.sqlite3"
            processor = WebhookProcessor(db_path, "secret")
            result = processor.process(
                {"Authorization": "Bearer secret"},
                json.dumps({
                    "id": "event-customer-new", "type": "customer", "action": "created",
                    "payload": {
                        "customerId": "enc-new-1", "customerFirstName": "Nina",
                        "customerLastName": "Diaz", "mobilePhone": "5550009999",
                        "email": "nina@example.com",
                    },
                }).encode(),
            )
            self.assertEqual(result["derived_table"], "customers")
            self.assertEqual(result["status"], "created")
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            saved = conn.execute(
                "SELECT first_name, mobile, sms_opt_out FROM customers WHERE enc_user_id='enc-new-1'"
            ).fetchone()
            self.assertEqual(saved[0], "Nina")
            self.assertEqual(saved[2], 0)  # opted in by default, matching the bulk import

    def test_customer_updated_webhook_never_touches_opt_out(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "zey.sqlite3"
            processor = WebhookProcessor(db_path, "secret")
            processor.store.sync_customers(pd.DataFrame([
                {"UserID": "cust-1", "encUserId": "enc-1", "Mobile": "5550000001", "FirstName": "Old"}
            ]))
            processor.store.set_opt_out("+15550000001")

            result = processor.process(
                {"Authorization": "Bearer secret"},
                json.dumps({
                    "id": "event-customer-upd", "type": "customer", "action": "updated",
                    "payload": {"customerId": "enc-1", "customerFirstName": "New", "city": "Dallas"},
                }).encode(),
            )
            self.assertEqual(result["status"], "updated")
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            saved = conn.execute(
                "SELECT first_name, city, sms_opt_out FROM customers WHERE enc_user_id='enc-1'"
            ).fetchone()
            self.assertEqual(saved[0], "New")
            self.assertEqual(saved[1], "Dallas")
            self.assertEqual(saved[2], 1)  # opt-out survives the webhook update untouched

    def test_customer_deleted_webhook_deactivates_only_that_customer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "zey.sqlite3"
            processor = WebhookProcessor(db_path, "secret")
            processor.store.sync_customers(pd.DataFrame([
                {"UserID": "cust-1", "encUserId": "enc-1", "Mobile": "5550000001"},
                {"UserID": "cust-2", "encUserId": "enc-2", "Mobile": "5550000002"},
            ]))

            result = processor.process(
                {"Authorization": "Bearer secret"},
                json.dumps({
                    "id": "event-customer-del", "type": "customer", "action": "deleted",
                    "payload": {"customerId": "enc-1"},
                }).encode(),
            )
            self.assertEqual(result["status"], "deactivated")
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            rows = dict(conn.execute("SELECT enc_user_id, active FROM customers").fetchall())
            self.assertEqual(rows["enc-1"], 0)
            self.assertEqual(rows["enc-2"], 1)  # the other customer is untouched


if __name__ == "__main__":
    unittest.main()
