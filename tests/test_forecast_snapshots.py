from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import forecast_snapshots  # noqa: E402
from sms_campaign import forecast_models  # noqa: E402

HAVE_MODELS = all(importlib.util.find_spec(m) for m in ("statsmodels", "prophet"))


class TestMissingDays(unittest.TestCase):
    def test_backfills_seven_months_and_skips_stored_and_too_early_mornings(self) -> None:
        today = date(2026, 11, 16)
        stored = {date(2026, 5, 1), date(2026, 11, 16)}
        days = forecast_snapshots.missing_days(stored, date(2025, 10, 15), today)
        self.assertEqual(days[0], date(2026, 5, 2))  # May 1 is stored
        self.assertEqual(days[-1], date(2026, 11, 15))  # today is stored
        self.assertEqual(len(days), (date(2026, 11, 15) - date(2026, 5, 2)).days + 1)
        # Data starting Oct 15 leaves nothing before Nov 12 (4 weeks of history).
        days = forecast_snapshots.missing_days(set(), date(2026, 10, 15), today)
        self.assertEqual(days[0], date(2026, 11, 12))


@unittest.skipUnless(HAVE_MODELS, "statsmodels and prophet are not installed")
class TestModels(unittest.TestCase):
    def test_steady_salon_is_forecast_near_its_daily_pattern(self) -> None:
        # $100 every day but Monday (closed), for 20 weeks.
        start, as_of = date(2026, 4, 1), date(2026, 9, 15)
        daily = {}
        d = start
        while d < as_of:
            daily[d] = 0.0 if d.weekday() == 0 else 100.0
            d += timedelta(days=1)

        days = forecast_models.predict_days(daily, as_of, date(2026, 9, 30))

        self.assertEqual(len(days), 16)
        self.assertTrue(all(v >= 0 for v in days))
        # 16 days with 2 Mondays: about $1,400.
        self.assertAlmostEqual(sum(days), 1400.0, delta=100.0)
        monday = days[(date(2026, 9, 21) - as_of).days]
        self.assertLess(monday, 30.0)


if __name__ == "__main__":
    unittest.main()
