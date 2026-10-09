from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import live_dashboard  # noqa: E402
from sms_campaign.db import get_connection  # noqa: E402


class TestWeekdayTable(unittest.TestCase):
    def _add(self, conn, sid: str, when: str, amount: float, customer_id=None, name=None) -> None:
        conn.execute(
            "INSERT INTO transactions (vagaro_transaction_id, transaction_date, total_amount, customer_id, customer_name) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, when, amount, customer_id, name),
        )

    def test_payments_land_on_the_chicago_weekday_and_customers_count_once_a_day(self) -> None:
        conn = get_connection()
        try:
            # Saturday 2026-09-05: the report import stored local time, the
            # webhook UTC (00:30 UTC on the 6th is 19:30 CDT on the 5th).
            self._add(conn, "v1", "2026-09-05T11:00:00.12", 40.0, customer_id=1)
            self._add(conn, "v2", "2026-09-06T00:30:00+00:00", 20.0, customer_id=1)
            self._add(conn, "v3", "2026-09-12T15:00:00.5", 30.0, name="Ana Lee")  # Saturday, no customer link
            self._add(conn, "v4", "2026-09-07T15:00:00+00:00", 50.0, customer_id=2)  # Monday
            self._add(conn, "v5", "2026-10-03T16:00:00+00:00", 10.0, customer_id=2)  # Saturday, current month
            # Two lines of one walk-in checkout with no customer: one customer.
            self._add(conn, "v6", "2026-10-03T17:00:00+00:00", 12.0)
            self._add(conn, "v7", "2026-10-03T17:00:00+00:00", 36.0)
            conn.commit()
            rows = live_dashboard.fetch_weekday_rows(conn)
        finally:
            conn.close()

        matrix = live_dashboard.weekday_matrix(rows, "2026-10-08")
        self.assertEqual([m["month"] for m in matrix["months"]], ["2026-10", "2026-09"])
        october, september = matrix["months"]
        self.assertTrue(october["partial"])
        self.assertTrue(september["partial"])  # data starts on the 5th
        self.assertEqual(matrix["firstDay"], date(2026, 9, 5))

        saturday = september["cells"][5]
        self.assertEqual((saturday["revenue"], saturday["customers"], saturday["openDays"]), (90.0, 2, 2))
        self.assertEqual(saturday["avgRevenue"], 45.0)
        self.assertEqual(september["cells"][0]["revenue"], 50.0)
        self.assertIsNone(september["cells"][1])
        self.assertEqual(september["total"]["revenue"], 140.0)
        self.assertEqual(matrix["weekdays"][5]["openDays"], 3)
        self.assertEqual(matrix["overall"]["revenue"], 198.0)
        self.assertEqual(october["cells"][5]["customers"], 2)

        page = live_dashboard.render_weekday_table(matrix)
        self.assertIn("<th>Cmt</th>", page)
        self.assertIn("$90", page)

    def test_year_heatmap_places_days_by_week_and_shades_by_quartile(self) -> None:
        conn = get_connection()
        try:
            for i, (day, amount) in enumerate([("2026-10-03", 100.0), ("2026-10-02", 200.0), ("2026-10-01", 300.0),
                                               ("2026-09-30", 400.0), ("2026-09-29", 500.0)]):
                self._add(conn, f"h{i}", f"{day}T16:00:00+00:00", amount, customer_id=i + 1)
            conn.commit()
            today = date(2026, 10, 8)  # Thursday
            rows = live_dashboard.fetch_daily_rows(conn, live_dashboard.heatmap_start(today))
        finally:
            conn.close()

        hm = live_dashboard.year_heatmap(rows, today)
        self.assertEqual(hm["days"][0]["day"], date(2026, 10, 5) - timedelta(weeks=52))
        self.assertEqual(len(hm["days"]), 52 * 7 + 4)
        self.assertEqual((hm["days"][-1]["week"], hm["days"][-1]["dow"]), (52, 3))
        by_day = {x["day"]: x for x in hm["days"]}
        self.assertEqual(by_day[date(2026, 10, 3)]["levelRev"], 1)
        self.assertEqual(by_day[date(2026, 9, 29)]["levelRev"], 4)
        self.assertEqual(by_day[date(2026, 10, 4)]["levelRev"], 0)  # Sunday, no payments
        self.assertFalse(by_day[date(2026, 10, 4)]["noData"])
        self.assertTrue(by_day[date(2026, 9, 1)]["noData"])  # before the first payment
        self.assertEqual((hm["openDays"], hm["revenue"], hm["customers"]), (5, 1500.0, 5))
        self.assertEqual(hm["busiest"]["day"], date(2026, 9, 29))

        page = live_dashboard.render_year_heatmap(hm)
        self.assertIn("Salı 29.09.2026: $500.00 ciro, 1 müşteri", page)


if __name__ == "__main__":
    unittest.main()
