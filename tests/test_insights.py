from __future__ import annotations

import unittest
from datetime import date, timedelta

from sms_campaign import insights

D = date(2026, 11, 16)


def visit(cid: str, day: date, *services: str) -> list[dict]:
    return [{"customer_id": cid, "day": day, "service_name": s} for s in services]


class TestAttachAndToday(unittest.TestCase):
    def setUp(self) -> None:
        # 25 brow visits, 5 of them with tint; one regular who always adds lips.
        self.history = []
        for i in range(25):
            extra = ("Tint",) if i < 5 else ()
            self.history += visit(f"c{i}", D - timedelta(days=40), "Brow", *extra)
        for k in range(3):
            self.history += visit("reg", D - timedelta(days=30 * (k + 1)), "Brow", "Lips")

    def test_attach_rate_is_the_share_of_a_services_visits(self) -> None:
        rates = insights.attach_rates(self.history)
        tint = next(r for r in rates["Brow"] if r[0] == "Tint")
        self.assertAlmostEqual(tint[1], 5 / 28)
        self.assertNotIn("Tint", rates)  # too few tint visits to quote

    def test_offers_their_own_habit_first_then_what_others_add(self) -> None:
        today = [
            {"customer_id": "reg", "name": "Regular", "time": "10:00", "service_name": "Brow",
             "status": "Service Completed"},
            {"customer_id": "new", "name": "New", "time": "11:00", "service_name": "Brow", "status": "Confirmed"},
            {"customer_id": "gone", "name": "Gone", "time": "12:00", "service_name": "Brow", "status": "Cancel"},
        ]
        rows = insights.todays_customers(today, self.history, {"reg": D + timedelta(days=30)}, 35,
                                         insights.attach_rates(self.history))
        reg, new = rows
        self.assertEqual(len(rows), 2)  # the cancellation is left out
        self.assertFalse(reg["first_time"])
        self.assertEqual(reg["visits_before"], 3)
        self.assertEqual(reg["usual_gap"], 30)
        self.assertTrue(reg["offers"][0].startswith("Lips"))
        self.assertTrue(reg["done"])
        self.assertEqual(reg["next_booking"], D + timedelta(days=30))
        self.assertTrue(new["first_time"])
        self.assertTrue(new["offers"][0].startswith("Tint"))
        self.assertIsNone(new["next_booking"])
        self.assertEqual(new["usual_gap"], 35)


class TestLeakageAndCampaigns(unittest.TestCase):
    def test_a_cancellation_is_lost_only_without_a_visit_within_two_weeks(self) -> None:
        cancels = [
            {"customer_id": "a", "day": D - timedelta(days=30), "amount": 30.0},  # came back
            {"customer_id": "b", "day": D - timedelta(days=30), "amount": 45.0},  # lost
            {"customer_id": "c", "day": D - timedelta(days=3), "amount": 20.0},   # too soon to say
        ]
        visits = {"a": [D - timedelta(days=25)], "b": [D - timedelta(days=60)]}
        m = insights.cancel_leakage(cancels, visits, D)
        oct_, nov = m["2026-10"], m["2026-11"]
        self.assertEqual((oct_["count"], oct_["lost"], oct_["lostValue"]), (2, 1, 45.0))
        self.assertEqual((nov["count"], nov["lost"], nov["pending"]), (1, 0, 1))

    def test_lost_cancellations_list_only_those_never_back_or_booked(self) -> None:
        cancels = [
            {"customer_id": "a", "day": D - timedelta(days=40)},  # came back later
            {"customer_id": "b", "day": D - timedelta(days=50)},  # gone
            {"customer_id": "b", "day": D - timedelta(days=30)},  # gone, later cancellation
            {"customer_id": "c", "day": D - timedelta(days=40)},  # booked again
            {"customer_id": "d", "day": D - timedelta(days=5)},   # too recent
        ]
        visits = {"a": [D - timedelta(days=20)], "b": [D - timedelta(days=90)]}
        rows = insights.lost_cancellations(cancels, visits, {"c": D + timedelta(days=3)}, D)
        self.assertEqual([(r["customer_id"], r["day"]) for r in rows], [("b", D - timedelta(days=30))])

    def test_scorecard_counts_returns_after_the_text_and_a_baseline_without_it(self) -> None:
        send_day = D - timedelta(days=30)
        sends = [{"customer_id": "t1", "campaign_type": "Campaign", "day": send_day},
                 {"customer_id": "t2", "campaign_type": "Campaign", "day": send_day},
                 {"customer_id": "late", "campaign_type": "Campaign", "day": D - timedelta(days=2)}]
        last = send_day - timedelta(days=70)
        visits = [{"customer_id": c, "day": last, "amount": 30.0} for c in ("t1", "t2", "u1", "u2", "u3", "u4")]
        visits.append({"customer_id": "t1", "day": send_day + timedelta(days=5), "amount": 50.0})
        visits.append({"customer_id": "t2", "day": send_day, "amount": 40.0})  # same day: not counted
        visits.append({"customer_id": "u1", "day": send_day + timedelta(days=3), "amount": 30.0})
        row = insights.campaign_scorecard(sends, visits, D)[0]
        self.assertEqual((row["texted"], row["back"], row["revenue"]), (2, 1, 50.0))
        # Untexted customers 70 days out on the send day: 1 of u1-u4 came back.
        self.assertEqual(row["baselineN"], 4)
        self.assertAlmostEqual(row["baseline"], 0.25)

    def test_soft_days_rank_the_weakest_open_day_first(self) -> None:
        days = [(D + timedelta(days=1), 900.0), (D + timedelta(days=2), 0.0), (D + timedelta(days=3), 500.0)]
        rows = insights.soft_days(days, 750.0, {D + timedelta(days=3): 2})
        self.assertEqual([r["day"] for r in rows], [D + timedelta(days=3), D + timedelta(days=1)])
        self.assertEqual(rows[0]["booked"], 2)
        self.assertAlmostEqual(rows[0]["ratio"], 500 / 750)

    def test_rebook_rate(self) -> None:
        self.assertEqual(insights.rebook_rate({"a", "b", "c"}, {"a": D}), (1, 3))


if __name__ == "__main__":
    unittest.main()
