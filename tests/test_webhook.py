from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from sms_campaign.db import get_connection
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

    def test_multi_line_item_checkout_does_not_overwrite_earlier_line_item(self) -> None:
        """Regression test for the 2026-09-16 incident: Vagaro's transactionId
        identifies the whole checkout and repeats across every line item when
        a checkout sells more than one service. Using it as our uniqueness
        key made a second line item's webhook silently overwrite the first
        (found live: 30/83 checkouts had 2+ line items, 40 transactions lost).
        userPaymentId is unique per line item and must be used instead. Also
        covers: subtotal backed out from total/tax/tip when Vagaro omits it,
        and customer_name backfilled from the resolved customer record."""
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "zey.sqlite3"
            processor = WebhookProcessor(db_path, "secret")
            processor.store.sync_customers(
                pd.DataFrame([{"UserID": "cust-1", "Mobile": "5550000001", "FirstName": "Ana"}])
            )
            base_payload = {
                "transactionId": "shared-checkout-1",
                "transactionDate": "2026-09-16T15:46:51.3Z",
                "customerId": "cust-1",
                "purchaseType": "Service",
                "tax": 0,
                "discount": 0,
            }
            first = processor.process(
                {"X-Vagaro-Verification-Token": "secret"},
                json.dumps({
                    "id": "event-line-1", "type": "transaction", "action": "created",
                    "payload": {**base_payload, "userPaymentId": "pay-1",
                                "itemSold": "Brow Shaping", "ccAmount": 36, "tip": 6},
                }).encode(),
            )
            second = processor.process(
                {"X-Vagaro-Verification-Token": "secret"},
                json.dumps({
                    "id": "event-line-2", "type": "transaction", "action": "created",
                    "payload": {**base_payload, "userPaymentId": "pay-2",
                                "itemSold": "Lips", "ccAmount": 12, "tip": 2},
                }).encode(),
            )
            self.assertEqual(first["derived_table"], "transactions")
            self.assertEqual(second["derived_table"], "transactions")

            table = processor.store.export_table("transactions").sort_values("total_amount")
            self.assertEqual(len(table), 2, "both line items must survive as separate rows")

            lips = table.iloc[0]
            self.assertEqual(lips["total_amount"], 12.0)
            self.assertEqual(lips["subtotal"], 10.0)  # 12 - 0 tax - 2 tip + 0 discount
            self.assertEqual(lips["customer_name"], "Ana")

            brows = table.iloc[1]
            self.assertEqual(brows["total_amount"], 36.0)
            self.assertEqual(brows["subtotal"], 30.0)
            self.assertEqual(brows["customer_name"], "Ana")

    def test_transaction_for_unknown_customer_creates_it_via_vagaro_api(self) -> None:
        """Regression test for the 2026-09-16 finding: Vagaro doesn't
        reliably send a 'customer' webhook for a walk-in who books/pays
        directly, leaving the transaction permanently unlinked. When the
        API is reachable, fetch and create the customer instead."""
        with TemporaryDirectory() as temp_dir, patch(
            "sms_campaign.webhook.vagaro_api.fetch_customer",
            return_value={
                "customerId": "unknown-cust-1",
                "customerFirstName": "Nina",
                "customerLastName": "Diaz",
                "mobilePhone": "5550009999",
                "email": "nina@example.com",
            },
        ):
            db_path = Path(temp_dir) / "zey.sqlite3"
            processor = WebhookProcessor(db_path, "secret")
            event = {
                "id": "event-unknown-cust", "type": "transaction", "action": "created",
                "payload": {
                    "transactionId": "txn-unknown-1", "userPaymentId": "pay-unknown-1",
                    "transactionDate": "2026-09-16T18:59:00Z", "customerId": "unknown-cust-1",
                    "itemSold": "Brow shaping", "ccAmount": 25.0,
                },
            }
            result = processor.process(
                {"X-Vagaro-Verification-Token": "secret"}, json.dumps(event).encode()
            )
            self.assertEqual(result["derived_table"], "transactions")

            table = processor.store.export_table("transactions")
            self.assertEqual(table.iloc[0]["customer_name"], "Nina Diaz")
            self.assertIsNotNone(table.iloc[0]["customer_id"])

            customers = processor.store.get_active_customers()
            self.assertEqual(customers.iloc[0]["mobile"], "+15550009999")

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

            conn = get_connection()
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM webhook_events").fetchone()["c"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"], 0)
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
            conn = get_connection()
            saved = conn.execute(
                "SELECT first_name, mobile, sms_opt_out FROM customers WHERE enc_user_id='enc-new-1'"
            ).fetchone()
            conn.close()
            self.assertEqual(saved["first_name"], "Nina")
            self.assertEqual(saved["sms_opt_out"], 0)  # opted in by default, matching the bulk import

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
            conn = get_connection()
            saved = conn.execute(
                "SELECT first_name, city, sms_opt_out FROM customers WHERE enc_user_id='enc-1'"
            ).fetchone()
            conn.close()
            self.assertEqual(saved["first_name"], "New")
            self.assertEqual(saved["city"], "Dallas")
            self.assertEqual(saved["sms_opt_out"], 1)  # opt-out survives the webhook update untouched

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
            conn = get_connection()
            rows = {
                row["enc_user_id"]: row["active"]
                for row in conn.execute("SELECT enc_user_id, active FROM customers").fetchall()
            }
            conn.close()
            self.assertEqual(rows["enc-1"], 0)
            self.assertEqual(rows["enc-2"], 1)  # the other customer is untouched

    def test_appointment_booking_status_is_kept_and_deleted_is_marked_not_removed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            processor = WebhookProcessor(Path(temp_dir) / "zey.sqlite3", "secret")
            appointment = {
                "appointmentId": "appt-1", "customerId": "enc-1", "serviceTitle": "Brow Shaping",
                "startTime": "2026-10-06T21:00:00Z", "endTime": "2026-10-06T21:15:00Z",
                "amount": 30.0, "bookingStatus": "Confirmed",
            }

            def send(event_id: str, action: str, **changes: object) -> None:
                processor.process(
                    {"Authorization": "Bearer secret"},
                    json.dumps({
                        "id": event_id, "type": "appointment", "action": action,
                        "payload": {**appointment, **changes},
                    }).encode(),
                )

            def status() -> str | None:
                conn = get_connection()
                row = conn.execute(
                    "SELECT booking_status FROM services WHERE vagaro_appt_id='appt-1'"
                ).fetchone()
                conn.close()
                return row["booking_status"]

            send("evt-appt-1", "created")
            self.assertEqual(status(), "Confirmed")
            send("evt-appt-2", "updated", bookingStatus="Service Completed")
            self.assertEqual(status(), "Service Completed")
            send("evt-appt-3", "deleted", bookingStatus=None)
            self.assertEqual(status(), "Deleted")

    def test_customer_webhook_relinks_appointment_and_transaction_saved_before_it(self) -> None:
        """Regression test for the 2026-10-07 finding: an appointment and
        checkout that arrive before their customer exists were left with
        no customer forever, even after the customer record was created."""
        with TemporaryDirectory() as temp_dir:
            processor = WebhookProcessor(Path(temp_dir) / "zey.sqlite3", "secret")
            headers = {"Authorization": "Bearer secret"}
            processor.process(headers, json.dumps({
                "id": "evt-orphan-appt", "type": "appointment", "action": "created",
                "payload": {
                    "appointmentId": "appt-orphan", "customerId": "enc-late",
                    "serviceTitle": "Threading - Lips", "startTime": "2026-10-06T21:15:00Z",
                    "endTime": "2026-10-06T21:25:00Z", "amount": 10.0,
                    "bookingStatus": "Service Completed",
                },
            }).encode())
            processor.process(headers, json.dumps({
                "id": "evt-orphan-txn", "type": "transaction", "action": "created",
                "payload": {
                    "transactionId": "txn-orphan", "userPaymentId": "pay-orphan",
                    "transactionDate": "2026-10-06T21:37:00Z", "customerId": "enc-late",
                    "itemSold": "Threading - Lips", "ccAmount": 12.0,
                },
            }).encode())

            conn = get_connection()
            self.assertIsNone(conn.execute("SELECT customer_id FROM services").fetchone()["customer_id"])
            self.assertIsNone(conn.execute("SELECT customer_id FROM transactions").fetchone()["customer_id"])
            conn.close()

            result = processor.process(headers, json.dumps({
                "id": "evt-late-customer", "type": "customer", "action": "created",
                "payload": {
                    "customerId": "enc-late", "customerFirstName": "Lena",
                    "customerLastName": "Park", "mobilePhone": "5550004444",
                },
            }).encode())
            self.assertEqual(result["relinked"], {"services": 1, "transactions": 1})

            conn = get_connection()
            customer_id = conn.execute(
                "SELECT customer_id FROM customers WHERE enc_user_id='enc-late'"
            ).fetchone()["customer_id"]
            service = conn.execute("SELECT customer_id FROM services").fetchone()
            txn = conn.execute("SELECT customer_id, customer_name FROM transactions").fetchone()
            conn.close()
            self.assertEqual(service["customer_id"], customer_id)
            self.assertEqual(txn["customer_id"], customer_id)
            self.assertEqual(txn["customer_name"], "Lena Park")

    def test_corrected_number_from_vagaro_clears_the_undeliverable_flag(self) -> None:
        with TemporaryDirectory() as temp_dir:
            processor = WebhookProcessor(Path(temp_dir) / "zey.sqlite3", "secret")

            def customer_event(event_id: str, action: str, phone: str) -> None:
                processor.process({"Authorization": "Bearer secret"}, json.dumps({
                    "id": event_id, "type": "customer", "action": action,
                    "payload": {"customerId": "enc-fix", "customerFirstName": "Ida", "mobilePhone": phone},
                }).encode())

            def flag() -> str | None:
                conn = get_connection()
                row = conn.execute("SELECT sms_undeliverable FROM customers WHERE enc_user_id='enc-fix'").fetchone()
                conn.close()
                return row["sms_undeliverable"]

            customer_event("evt-fix-1", "created", "5550001111")
            conn = get_connection()
            conn.execute("UPDATE customers SET sms_undeliverable='geçersiz numara' WHERE enc_user_id='enc-fix'")
            conn.commit()
            conn.close()
            customer_event("evt-fix-2", "updated", "5550001111")
            self.assertEqual(flag(), "geçersiz numara")  # same number: still undeliverable
            customer_event("evt-fix-3", "updated", "5550002222")
            self.assertIsNone(flag())

    def test_relink_skips_a_stored_payload_postgres_cannot_parse(self) -> None:
        """One unparseable stored payload must not break relinking (or the
        panel query sharing the same pattern) for every other row."""
        with TemporaryDirectory() as temp_dir:
            processor = WebhookProcessor(Path(temp_dir) / "zey.sqlite3", "secret")
            conn = get_connection()
            conn.execute(
                "INSERT INTO webhook_events (event_id, event_type, action, payload_json) "
                "VALUES ('evt-bad', 'appointment', 'created', '{\"payload\": {\"amount\": NaN}}')"
            )
            conn.commit()
            conn.close()
            self.assertEqual(processor.store.relink_orphans(), {"services": 0, "transactions": 0})


if __name__ == "__main__":
    unittest.main()
