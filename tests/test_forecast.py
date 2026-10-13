from __future__ import annotations

import unittest
from datetime import date, timedelta

from sms_campaign import forecast


def steady_salon(start: date, end: date, today: tuple[float, int]) -> dict:
    """$100 and 2 visits every day but Monday (closed), and today's takings so far."""
    daily = {}
    d = start
    while d < end:
        if d.weekday() != 0:
            daily[d] = (100.0, 2)
        d += timedelta(days=1)
    daily[end] = today
    return daily


class TestForecast(unittest.TestCase):
    def test_steady_month_is_forecast_exactly_with_a_zero_width_range(self) -> None:
        today = date(2026, 9, 15)  # Tuesday
        daily = steady_salon(date(2026, 6, 1), today, (30.0, 1))
        # Half of the visits are distinct customers (Jun 50, Jul 54, Aug 52 visits).
        monthly_customers = {"2026-06": 25, "2026-07": 27, "2026-08": 26, "2026-09": 20}

        fc = forecast.forecast(daily, monthly_customers, today)

        # September has 26 open days: 12 before today, today, 13 after.
        self.assertAlmostEqual(fc["actualRevenue"], 1230.0)
        self.assertAlmostEqual(fc["revenue"], 2600.0)  # today counts for its remaining $70
        self.assertEqual(fc["openDaysLeft"], 14)
        self.assertEqual((fc["revenueLow"], fc["revenueHigh"]), (fc["revenue"], fc["revenue"]))
        self.assertEqual(fc["revenueMape"], 0.0)
        self.assertEqual([t["month"] for t in fc["backtests"]], ["2026-08", "2026-07"])
        self.assertAlmostEqual(fc["customers"], 26.0)  # 52 projected visits x 0.5
        self.assertAlmostEqual(fc["customersMape"], 0.0)
        self.assertAlmostEqual(fc["neededPerOpenDay"], (2600.0 - 1230.0) / 14)
        self.assertAlmostEqual(fc["recentPerOpenDay"], 100.0)

    def test_no_forecast_without_four_weeks_of_history(self) -> None:
        today = date(2026, 9, 15)
        daily = steady_salon(date(2026, 8, 25), today, (0.0, 0))
        self.assertIsNone(forecast.forecast(daily, {}, today))

    def test_range_always_contains_the_forecast_and_never_drops_below_what_is_paid(self) -> None:
        # Both past months beat the forecast: the low end stays at the forecast.
        low, high, mape = forecast._range(100.0, 50.0, 80.0, [(90.0, 40.0, 110.0), (95.0, 45.0, 120.0)])
        self.assertEqual(low, 100.0)
        self.assertAlmostEqual(high, 100.0 + 25 / 45 * 50)
        self.assertAlmostEqual(mape, (20 / 110 + 25 / 120) / 2)
        # A big past miss can't push the low end under this month's actual.
        low, _, _ = forecast._range(100.0, 50.0, 99.0, [(100.0, 50.0, 40.0)])
        self.assertEqual(low, 99.0)


if __name__ == "__main__":
    unittest.main()
