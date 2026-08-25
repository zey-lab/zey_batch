from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from sms_campaign.data_store import ZeyDataStore


class TestTransactionsSync(unittest.TestCase):
    def test_new_transactions_are_inserted_and_linked_to_matching_customer(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
            store.sync_customers(pd.DataFrame([{"Mobile": "5550000001", "FirstName": "Ana"}]))

            result = store.sync_transactions(
                pd.DataFrame(
                    [
                        {
                            "TransactionID": "TXN-1",
                            "Mobile": "5550000001",
                            "TransactionDate": "2026-08-01",
                            "TransactionType": "Sale",
                            "PaymentMethod": "Visa",
                            "SubTotal": "40.00",
                            "Tax": "3.30",
                            "Tip": "8.00",
                            "Total": "51.30",
                        },
                        {
                            "TransactionID": "TXN-2",
                            "CustomerName": "Walk-in",
                            "TransactionDate": "2026-08-02",
                            "TransactionType": "Sale",
                            "Total": "25.00",
                        },
                    ]
                )
            )

            table = store.export_table("transactions")

        self.assertEqual(result.inserted, 2)
        self.assertEqual(result.updated, 0)
        linked = table[table["vagaro_transaction_id"] == "TXN-1"].iloc[0]
        self.assertIsNotNone(linked["customer_id"])
        self.assertEqual(linked["total_amount"], 51.30)
        unlinked = table[table["vagaro_transaction_id"] == "TXN-2"].iloc[0]
        self.assertTrue(pd.isna(unlinked["customer_id"]))
        self.assertEqual(unlinked["customer_name"], "Walk-in")

    def test_reimporting_the_same_transaction_id_updates_instead_of_duplicating(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
            store.sync_transactions(
                pd.DataFrame([{"TransactionID": "TXN-1", "Total": "10.00", "Status": "Pending"}])
            )
            result = store.sync_transactions(
                pd.DataFrame([{"TransactionID": "TXN-1", "Total": "10.00", "Status": "Completed"}])
            )

            table = store.export_table("transactions")

        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.updated, 1)
        self.assertEqual(len(table), 1)
        self.assertEqual(table.iloc[0]["status"], "Completed")

    def test_rows_without_a_transaction_id_are_skipped_as_errors(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
            result = store.sync_transactions(pd.DataFrame([{"Total": "10.00"}]))

        self.assertEqual(result.inserted, 0)
        self.assertEqual(len(result.errors or []), 1)

    def test_empty_dataframe_is_a_no_op(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
            result = store.sync_transactions(pd.DataFrame())

        self.assertEqual(result.inserted, 0)
        self.assertEqual(result.updated, 0)

    def test_get_stats_reports_transactions_total(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
            store.sync_transactions(
                pd.DataFrame([{"TransactionID": "TXN-1", "Total": "10.00"}])
            )
            stats = store.get_stats()

        self.assertEqual(stats["transactions_total"], 1)


if __name__ == "__main__":
    unittest.main()
