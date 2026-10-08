from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_sms_status  # noqa: E402
from sms_campaign.data_store import ZeyDataStore  # noqa: E402
from sms_campaign.db import get_connection  # noqa: E402

STOP_ERROR = "Twilio error: Unable to create record: Attempt to send to unsubscribed recipient"


class TestSyncSmsStatus(unittest.TestCase):
    def _store(self, temp_dir: str) -> ZeyDataStore:
        store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
        store.sync_customers(pd.DataFrame([
            {"UserID": "V-1", "Mobile": "5550000001", "FirstName": "Ana"},
            {"UserID": "V-2", "Mobile": "5550000002", "FirstName": "Bea"},
        ]))
        return store

    def _customer_id(self, mobile: str) -> int:
        conn = get_connection()
        try:
            return conn.execute("SELECT customer_id FROM customers WHERE mobile=%s", (mobile,)).fetchone()["customer_id"]
        finally:
            conn.close()

    def test_queued_message_takes_twilios_current_status(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            cid = self._customer_id("+15550000001")
            store.log_sms(customer_id=cid, campaign_type="Review", message_text="hi", status="queued", twilio_sid="SM1")
            store.log_sms(customer_id=cid, campaign_type="Review", message_text="hi", status="sent", twilio_sid="SM2")
            client = Mock()
            client.messages.list.return_value = [
                SimpleNamespace(sid="SM1", status="delivered", error_code=None),
                SimpleNamespace(sid="SM2", status="undelivered", error_code=30005),
            ]
            result = sync_sms_status.refresh_statuses(client, "+15559999999")
            self.assertEqual(result["updated"], 2)
            rows = {r["twilio_sid"]: r for r in store.export_table("sms_history").to_dict("records")}
            self.assertEqual(rows["SM1"]["status"], "delivered")
            self.assertEqual(rows["SM2"]["status"], "undelivered")
            self.assertIn("30005", rows["SM2"]["error_message"])

    def test_older_messages_are_refreshed_only_when_asked(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            cid = self._customer_id("+15550000001")
            store.log_sms(customer_id=cid, campaign_type="Campaign", message_text="hi", status="queued", twilio_sid="SM9")
            conn = get_connection()
            conn.execute("UPDATE sms_history SET sent_at=(now() - interval '20 days')::text WHERE twilio_sid='SM9'")
            conn.commit()
            conn.close()
            client = Mock()
            client.messages.list.return_value = [SimpleNamespace(sid="SM9", status="delivered", error_code=None)]

            self.assertEqual(sync_sms_status.refresh_statuses(client, "+15559999999")["updated"], 0)
            self.assertEqual(sync_sms_status.refresh_statuses(client, "+15559999999", days=30)["updated"], 1)
            rows = {r["twilio_sid"]: r for r in store.export_table("sms_history").to_dict("records")}
            self.assertEqual(rows["SM9"]["status"], "delivered")

    def test_only_customers_whose_latest_sms_hit_stop_are_opted_out(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            stopped, resubscribed = self._customer_id("+15550000001"), self._customer_id("+15550000002")
            store.log_sms(customer_id=stopped, campaign_type="Campaign", message_text="hi",
                          status="failed", error_message=STOP_ERROR)
            store.log_sms(customer_id=resubscribed, campaign_type="Campaign", message_text="hi",
                          status="failed", error_message=STOP_ERROR)
            # A later successful send means the number replied START since.
            store.log_sms(customer_id=resubscribed, campaign_type="Campaign", message_text="hi", status="delivered")

            result = sync_sms_status.sync_stop_opt_outs(store)
            self.assertEqual(result["customer_ids"], [stopped])
            customers = store.export_table("customers").set_index("customer_id")
            self.assertEqual(customers.loc[stopped, "sms_opt_out"], 1)
            self.assertNotEqual(customers.loc[resubscribed, "sms_opt_out"], 1)


    def test_dead_numbers_are_marked_but_a_single_temporary_failure_is_not(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            dead, busy = self._customer_id("+15550000001"), self._customer_id("+15550000002")
            store.log_sms(customer_id=dead, campaign_type="Campaign", message_text="hi", status="failed",
                          error_message="Twilio error: Unable to create record: Invalid 'To' Phone Number: +1555")
            store.log_sms(customer_id=busy, campaign_type="Campaign", message_text="hi", status="undelivered",
                          error_message="Message status: undelivered (Twilio error 30003)")

            result = sync_sms_status.mark_undeliverable()
            self.assertEqual(result["reasons"], {"geçersiz numara": 1})
            customers = store.export_table("customers").set_index("customer_id")
            self.assertEqual(customers.loc[dead, "sms_undeliverable"], "geçersiz numara")
            self.assertIsNone(customers.loc[busy, "sms_undeliverable"])

            # The third failure in a row marks the temporary one too.
            for _ in range(2):
                store.log_sms(customer_id=busy, campaign_type="Campaign", message_text="hi", status="undelivered",
                              error_message="Message status: undelivered (Twilio error 30003)")
            sync_sms_status.mark_undeliverable()
            customers = store.export_table("customers").set_index("customer_id")
            self.assertEqual(customers.loc[busy, "sms_undeliverable"], "son 3 SMS teslim edilemedi")


if __name__ == "__main__":
    unittest.main()
