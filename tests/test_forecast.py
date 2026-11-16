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

    def test_daily_history_replays_each_morning_and_shows_the_change_from_the_day_before(self) -> None:
        today = date(2026, 9, 15)
        daily = steady_salon(date(2026, 6, 1), today, (30.0, 1))
        daily[date(2026, 9, 8)] = (800.0, 2)  # a $700 windfall on a Tuesday

        history = forecast.daily_history(daily, today, months=2)

        self.assertEqual([h["month"] for h in history], ["2026-09", "2026-08"])
        sep, aug = history
        # Today's takings so far are not known on the morning of today.
        self.assertEqual([p["day"].day for p in sep["days"]], list(range(1, 16)))
        self.assertIsNone(sep["finalRevenue"])
        by_day = {p["day"].day: p for p in sep["days"]}
        self.assertAlmostEqual(by_day[8]["revenue"], 2600.0)  # 26 open days x $100
        self.assertIsNone(by_day[1]["change"])
        # Sep 9: the $700 is paid, and the Tuesday average rises by $175 for
        # the 3 Tuesdays left (15, 22, 29).
        self.assertAlmostEqual(by_day[9]["revenue"], 2600.0 + 700 + 3 * 175)
        self.assertAlmostEqual(by_day[9]["change"], 700 + 3 * 175)
        self.assertAlmostEqual(by_day[9]["changePct"], (700 + 3 * 175) / 2600)
        # August is complete: every day, and its final revenue (26 open days).
        self.assertEqual(len(aug["days"]), 31)
        self.assertAlmostEqual(aug["finalRevenue"], 2600.0)
        self.assertTrue(all(p["revenue"] == 2600.0 for p in aug["days"]))

    def test_daily_history_is_empty_without_four_weeks_of_history(self) -> None:
        today = date(2026, 9, 15)
        daily = steady_salon(date(2026, 8, 25), today, (0.0, 0))
        self.assertEqual(forecast.daily_history(daily, today), [])

    def test_stored_model_run_replaces_the_weekday_means_and_today_counts_what_is_left(self) -> None:
        today = date(2026, 9, 15)  # Tuesday; $30 paid so far today
        daily = steady_salon(date(2026, 6, 1), today, (30.0, 1))
        monthly_customers = {"2026-06": 25, "2026-07": 27, "2026-08": 26, "2026-09": 20}
        # The model expects $150 every remaining day (16 days, today included).
        snapshots = {today: {"actualBefore": 1200.0, "days": [150.0] * 16}}
        # August as of the 15th: the model said $2,800, the month ended at $2,600.
        snapshots[date(2026, 8, 15)] = {"actualBefore": 1300.0, "days": [100.0] * 15}

        fc = forecast.forecast(daily, monthly_customers, today, snapshots)

        self.assertEqual(fc["model"], "snapshot")
        self.assertAlmostEqual(fc["actualRevenue"], 1230.0)
        # $120 still expected today, then 15 days x $150.
        self.assertAlmostEqual(fc["revenue"], 1230.0 + 120.0 + 15 * 150.0)
        aug = next(t for t in fc["backtests"] if t["month"] == "2026-08")
        self.assertAlmostEqual(aug["revenue"], 1300.0 + 15 * 100.0)
        self.assertAlmostEqual(aug["restRevenue"], 1500.0)
        # Months without a stored run fall back to the weekday means.
        jul = next(t for t in fc["backtests"] if t["month"] == "2026-07")
        self.assertAlmostEqual(jul["revenue"], 2700.0)  # July: 27 open days x $100
        self.assertEqual(forecast.forecast(daily, monthly_customers, today)["model"], "weekday4")

    def test_daily_history_uses_the_stored_morning_runs(self) -> None:
        today = date(2026, 9, 3)
        daily = steady_salon(date(2026, 6, 1), today, (0.0, 0))
        snapshots = {date(2026, 9, 2): {"actualBefore": 100.0, "days": [200.0] * 29}}
        sep = forecast.daily_history(daily, today, months=1, snapshots=snapshots)[0]["days"]
        self.assertAlmostEqual(sep[0]["revenue"], 2600.0)  # Sep 1: no run, weekday means
        self.assertAlmostEqual(sep[1]["revenue"], 100.0 + 29 * 200.0)
        self.assertAlmostEqual(sep[1]["change"], 100.0 + 29 * 200.0 - 2600.0)

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
