from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_dashboard  # noqa: E402

CT = ZoneInfo("America/Chicago")


class TestNoPaymentsAlert(unittest.TestCase):
    def alert(self, hour: int, revenue: float, expected: float | None):
        return live_dashboard.no_payments_alert(datetime(2026, 11, 17, hour, 5, tzinfo=CT), revenue, expected, 2, None)

    def test_warns_on_a_usually_open_afternoon_without_payments(self) -> None:
        a = self.alert(13, 0.0, 650.0)
        self.assertEqual((a["expected"], a["completed"]), (650.0, 2))
        self.assertIn("hiç ödeme gelmedi", live_dashboard.render_payments_alert(a))

    def test_quiet_in_the_morning_on_closed_days_and_once_paid(self) -> None:
        self.assertIsNone(self.alert(11, 0.0, 650.0))   # too early
        self.assertIsNone(self.alert(15, 0.0, 40.0))    # usually closed
        self.assertIsNone(self.alert(15, 0.0, None))    # no history
        self.assertIsNone(self.alert(15, 35.0, 650.0))  # a payment came in
        self.assertEqual(live_dashboard.render_payments_alert(None), "")


if __name__ == "__main__":
    unittest.main()
