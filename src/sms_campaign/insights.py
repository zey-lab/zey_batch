"""Action lists and scorecards for the live panel and the daily report.

Pure functions over rows the panel already loads, so they can be tested
without a database. A visit is one customer's payments (or booked services)
on one Chicago day. About half of the salon's customers walk in and most of
the rest book the same day, so these work from what happens today and from
past visits, not from bookings days ahead.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median

# A service needs this many visits before its add-on rates are quoted.
MIN_ATTACH_VISITS = 20
RETURN_WINDOW_DAYS = 14
# The automated win-back campaigns go to customers 60-365 days past their last
# visit and skip anyone texted in the last 15 days (campaigns table).
WINBACK_MIN_DAYS, WINBACK_MAX_DAYS, WINBACK_QUIET_DAYS = 60, 365, 15
ACTIVE_STATUSES_EXCLUDED = ("Cancel", "Deleted")


def attach_rates(history: list[dict]) -> dict[str, list[tuple[str, float, int]]]:
    """For each service, the other services bought in the same visit, most
    common first: {service: [(other, share of its visits, its visits)]}.
    history rows: customer_id, day, service_name (completed services)."""
    visits: dict[tuple, set[str]] = defaultdict(set)
    for r in history:
        visits[(r["customer_id"], r["day"])].add(r["service_name"])
    alone, pairs = Counter(), Counter()
    for services in visits.values():
        for a in services:
            alone[a] += 1
            for b in services:
                if a != b:
                    pairs[(a, b)] += 1
    out: dict[str, list[tuple[str, float, int]]] = defaultdict(list)
    for (a, b), n in pairs.items():
        if alone[a] >= MIN_ATTACH_VISITS:
            out[a].append((b, n / alone[a], alone[a]))
    for a in out:
        out[a].sort(key=lambda x: -x[1])
    return dict(out)


def todays_customers(today_rows: list[dict], history: list[dict], future: dict, typical_gap: float,
                     attach: dict[str, list[tuple[str, float, int]]]) -> list[dict]:
    """One row per customer seen today (booked, in the chair or paid), with
    what to offer at checkout. today_rows: customer_id, name, time,
    service_name, status. history: completed services before today
    (customer_id, day, service_name). future: customer_id -> next booked day
    after today."""
    past: dict = defaultdict(list)
    for r in history:
        past[r["customer_id"]].append(r)
    people: dict = {}
    for r in sorted(today_rows, key=lambda r: r["time"]):
        if r["status"] in ACTIVE_STATUSES_EXCLUDED:
            continue
        p = people.setdefault(r["customer_id"], {"customer_id": r["customer_id"], "name": r["name"],
                                                  "time": r["time"], "services": [], "done": False})
        if r["service_name"] not in p["services"]:
            p["services"].append(r["service_name"])
        p["done"] = p["done"] or r["status"] == "Service Completed"
    out = []
    for cid, p in people.items():
        rows = past.get(cid, [])
        days = sorted({r["day"] for r in rows})
        p["visits_before"] = len(days)
        p["first_time"] = cid is not None and not days
        p["last_visit"] = days[-1] if days else None
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        p["usual_gap"] = round(median(gaps)) if len(gaps) >= 2 else round(typical_gap)
        counts = Counter(r["service_name"] for r in rows)
        # Their own habits first: what they usually get but haven't booked today.
        offers = [f"{s} (önceki {n} ziyarette)" for s, n in counts.most_common() if s not in p["services"]][:2]
        if not offers:
            for s in p["services"]:
                for other, share, n in attach.get(s, []):
                    if other not in p["services"] and share >= 0.05:
                        offers.append(f"{other} ({s} alanların %{share * 100:.0f}'i alıyor)")
                        break
                if offers:
                    break
        p["offers"] = offers
        p["next_booking"] = future.get(cid)
        out.append(p)
    return out


def rebook_rate(recent_visitors: set, future: dict) -> tuple[int, int]:
    """(visitors of the last 7 days holding a future booking, visitors)."""
    return sum(1 for c in recent_visitors if c in future), len(recent_visitors)


def cancel_leakage(cancels: list[dict], visit_days: dict, today: date) -> dict[str, dict]:
    """Per month: cancelled or deleted services, and those whose customer did
    not come back within RETURN_WINDOW_DAYS (from the cancelled day; a visit
    the same day means the booking was only changed). Days still inside the
    window are left out of "lost". cancels rows: customer_id, day,
    amount (the booked price). visit_days: customer_id -> sorted visit days."""
    out: dict[str, dict] = {}
    for r in cancels:
        m = out.setdefault(r["day"].strftime("%Y-%m"), {"count": 0, "value": 0.0, "lost": 0, "lostValue": 0.0,
                                                         "pending": 0})
        m["count"] += 1
        m["value"] += r["amount"] or 0.0
        days = visit_days.get(r["customer_id"], [])
        if any(r["day"] <= d <= r["day"] + timedelta(days=RETURN_WINDOW_DAYS) for d in days):
            continue
        if today - r["day"] <= timedelta(days=RETURN_WINDOW_DAYS):
            m["pending"] += 1
            continue
        m["lost"] += 1
        m["lostValue"] += r["amount"] or 0.0
    return out


def lost_cancellations(cancels: list[dict], visit_days: dict, future: dict, today: date) -> list[dict]:
    """Customers who cancelled more than RETURN_WINDOW_DAYS ago and haven't
    been back or booked since: one row each, latest cancellation first."""
    latest: dict = {}
    for r in cancels:
        if r["customer_id"] is None or today - r["day"] <= timedelta(days=RETURN_WINDOW_DAYS):
            continue
        if any(d >= r["day"] for d in visit_days.get(r["customer_id"], [])) or r["customer_id"] in future:
            continue
        if r["customer_id"] not in latest or r["day"] > latest[r["customer_id"]]["day"]:
            latest[r["customer_id"]] = r
    return sorted(latest.values(), key=lambda r: r["day"], reverse=True)


def campaign_scorecard(sends: list[dict], visits: list[dict], today: date) -> list[dict]:
    """Per campaign type over the sends given: customers texted, the share
    who visited within RETURN_WINDOW_DAYS and the revenue of those visits.
    For the win-back types the baseline is customers in the same 60-365 day
    band on each send day who weren't texted around it: the return rate
    without the message. Sends younger than the window are left out.
    sends rows: customer_id, campaign_type, day. visits rows: customer_id,
    day, amount."""
    by_customer: dict = defaultdict(list)
    for v in visits:
        by_customer[v["customer_id"]].append((v["day"], v["amount"]))
    for c in by_customer:
        by_customer[c].sort()
    texted: dict = defaultdict(set)
    for s in sends:
        texted[s["customer_id"]].add(s["day"])
    cutoff = today - timedelta(days=RETURN_WINDOW_DAYS)

    def came_back(customer, day) -> tuple[bool, float]:
        # From the day after: the texts go out at noon, so a visit that day
        # may have come before the message.
        hits = [a for d, a in by_customer.get(customer, []) if day < d <= day + timedelta(days=RETURN_WINDOW_DAYS)]
        return bool(hits), sum(hits)

    out = []
    types = sorted({s["campaign_type"] for s in sends})
    for t in types:
        seen, back, revenue = set(), 0, 0.0
        send_days = set()
        for s in sends:
            if s["campaign_type"] != t or s["day"] > cutoff or (s["customer_id"], s["day"]) in seen:
                continue
            seen.add((s["customer_id"], s["day"]))
            send_days.add(s["day"])
            hit, amount = came_back(s["customer_id"], s["day"])
            back += hit
            revenue += amount
        row = {"type": t, "texted": len(seen), "back": back,
               "rate": back / len(seen) if seen else None, "revenue": revenue, "baseline": None, "baselineN": 0}
        if t == "Campaign" and send_days:
            pool_back = pool_n = 0
            for day in sorted(send_days)[::7]:  # a sample of send days, a week apart
                for c, vs in by_customer.items():
                    before = [d for d, _ in vs if d < day]
                    if not before or not WINBACK_MIN_DAYS <= (day - before[-1]).days <= WINBACK_MAX_DAYS:
                        continue
                    if any(abs((d - day).days) <= WINBACK_QUIET_DAYS for d in texted.get(c, ())):
                        continue
                    pool_n += 1
                    pool_back += came_back(c, day)[0]
            row["baseline"] = pool_back / pool_n if pool_n else None
            row["baselineN"] = pool_n
        out.append(row)
    return out


def soft_days(expected: list[tuple[date, float]], target: float | None, booked: dict) -> list[dict]:
    """The coming days with the revenue the forecast expects, against the
    day's target to beat last month; weakest (expected / target) first.
    Days the salon is usually closed (expected ~0) are left out."""
    rows = [{"day": d, "expected": e, "target": target, "booked": booked.get(d, 0),
             "ratio": e / target if target else None}
            for d, e in expected if e >= 25]
    return sorted(rows, key=lambda r: (r["ratio"] if r["ratio"] is not None else 9, r["day"]))
