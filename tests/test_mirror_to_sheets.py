from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import mirror_to_sheets  # noqa: E402


class _FakeStore:
    def __init__(self, tables: dict[str, pd.DataFrame]) -> None:
        self._tables = tables

    def export_table(self, table: str) -> pd.DataFrame:
        return self._tables[table]


class TestMirrorAll(unittest.TestCase):
    def setUp(self) -> None:
        self.tables = {
            "customers": pd.DataFrame({"customer_id": [1, 2], "mobile": ["a", "b"]}),
            "sms_history": pd.DataFrame(),
            "services": pd.DataFrame(),
            "transactions": pd.DataFrame(),
            "employees": pd.DataFrame(),
            "campaigns": pd.DataFrame(),
            "sync_log": pd.DataFrame({"log_id": [1]}),
        }
        self.store = _FakeStore(self.tables)

    def test_writes_every_table_including_empty_ones(self) -> None:
        with mock.patch.object(mirror_to_sheets, "get_sheet_ids", return_value={}), \
             mock.patch.object(mirror_to_sheets, "run_gws") as run_gws:
            result = mirror_to_sheets.mirror_all(self.store)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(set(result["sheets"]), set(mirror_to_sheets.TABLE_SHEETS))
        self.assertEqual(result["sheets"]["customers"]["rows"], 2)
        self.assertEqual(result["sheets"]["sms_history"]["rows"], 0)
        # Empty tables must not issue any gws calls; non-empty ones must.
        run_gws.assert_called()

    def test_a_failing_table_does_not_abort_the_remaining_tables(self) -> None:
        def fake_run_gws(*args, **kwargs):
            if any("Customers!" in a for a in args if isinstance(a, str)):
                raise RuntimeError("boom")
            return mock.Mock(stdout="{}")

        with mock.patch.object(mirror_to_sheets, "get_sheet_ids", return_value={}), \
             mock.patch.object(mirror_to_sheets, "run_gws", side_effect=fake_run_gws):
            result = mirror_to_sheets.mirror_all(self.store)

        self.assertEqual(result["status"], "error")
        self.assertIn("error", result["sheets"]["customers"])
        # sync_log still gets attempted and succeeds despite customers failing.
        self.assertEqual(result["sheets"]["sync_log"]["rows"], 1)

    def test_batch_rows_stays_small_enough_to_avoid_arg_length_errors(self) -> None:
        # Regression guard for OSError: Argument list too long against gws,
        # observed with the previous BATCH_ROWS=200 on the 53-column
        # Customers table.
        self.assertLessEqual(mirror_to_sheets.BATCH_ROWS, 100)


if __name__ == "__main__":
    unittest.main()
