from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from sms_campaign.customer_store import CustomerStore


class TestCustomerStore(unittest.TestCase):
    def test_full_snapshot_creates_normalized_customer_records(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = CustomerStore(Path(temp_dir) / "customers.sqlite3")
            result = store.sync_dataframe(
                pd.DataFrame(
                    [
                        {"Mobile": "(555) 000-0001", "First Name": "Ana", "Last Visited": "2026-08-01"},
                        {"Mobile": "5550000002", "First Name": "Ben", "Last Visited": "2026-08-02"},
                    ]
                )
            )

            ana = store.get_customer("+15550000001")
            ben = store.get_customer("+15550000002")

        self.assertEqual(result.inserted, 2)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.invalid, 0)
        self.assertEqual(ana["first_name"], "Ana")
        self.assertEqual(ana["last_visited"], "2026-08-01")
        self.assertTrue(ana["active_in_latest_export"])
        self.assertEqual(ben["first_name"], "Ben")

    def test_new_export_updates_source_fields_but_preserves_local_messaging_state(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = CustomerStore(Path(temp_dir) / "customers.sqlite3")
            store.sync_dataframe(
                pd.DataFrame(
                    [{"Mobile": "5550000001", "First Name": "Ana", "Last Visited": "2026-08-01"}]
                )
            )
            store.set_messaging_state(
                "+15550000001",
                last_sms_sent_date="2026-08-10T18:00:00-05:00",
                last_sms_status="sent",
                sms_opt_out="Yes",
                opt_out_date="2026-08-11",
            )

            result = store.sync_dataframe(
                pd.DataFrame(
                    [{"Mobile": "5550000001", "First Name": "Ana Maria", "Last Visited": "2026-08-12"}]
                )
            )
            customer = store.get_customer("+15550000001")

        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(customer["first_name"], "Ana Maria")
        self.assertEqual(customer["last_visited"], "2026-08-12")
        self.assertEqual(customer["last_sms_status"], "sent")
        self.assertEqual(customer["sms_opt_out"], "Yes")
        self.assertEqual(customer["opt_out_date"], "2026-08-11")

    def test_missing_customer_is_retained_but_marked_inactive_after_full_snapshot(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = CustomerStore(Path(temp_dir) / "customers.sqlite3")
            store.sync_dataframe(
                pd.DataFrame(
                    [
                        {"Mobile": "5550000001", "First Name": "Ana"},
                        {"Mobile": "5550000002", "First Name": "Ben"},
                    ]
                )
            )

            result = store.sync_dataframe(
                pd.DataFrame([{"Mobile": "5550000001", "First Name": "Ana"}])
            )
            missing_customer = store.get_customer("+15550000002")

        self.assertEqual(result.deactivated, 1)
        self.assertFalse(missing_customer["active_in_latest_export"])

    def test_rows_without_usable_mobile_numbers_are_skipped(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = CustomerStore(Path(temp_dir) / "customers.sqlite3")
            result = store.sync_dataframe(pd.DataFrame([{"Mobile": None, "First Name": "No Phone"}]))

        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.invalid, 1)


if __name__ == "__main__":
    unittest.main()
