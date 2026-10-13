"""Month-end forecast of revenue and paying customers, for the live panel
and the daily report.

The method is kept simple enough to check by hand:

- Revenue: what has been paid this month so far, plus, for every remaining
  day, the average revenue of that weekday over the 4 weeks before today.
  Closed days count as $0, so the usual closing day and recent closures are
  priced in. Today counts only for what it hasn't brought in yet. Replayed
  over Jan-Sep 2026, 4 weeks missed the final month by 8% on average, 8
  weeks by 12% and 12 weeks by 14%: the business is growing, so a longer
  window lags behind. Correcting by past misses made it worse.
- Paying customers: customer visits are projected the same way, then
  turned into distinct customers with the distinct-customers-per-visit
  ratio of the 3 months before, never below this month's count so far.
- Range and accuracy: the same method replayed on each of the last 6 full
  months as of the same day of the month, using only the data available
  then. The smallest and largest misses on the remaining days bound the
  range (which always includes the forecast itself); the average miss is
  the stated accuracy.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

LOOKBACK_WEEKS = 4
BACKTEST_MONTHS = 6
RATIO_MONTHS = 3

Daily = dict[date, tuple[float, float]]  # day -> (revenue, customer visits)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def prev_month(d: date) -> date:
    """First day of the month before d's month."""
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def month_totals(daily: Daily, first: date) -> tuple[float, float]:
    days = [v for d, v in daily.items() if first <= d <= month_end(first)]
    return sum(v[0] for v in days), sum(v[1] for v in days)


def weekday_means(daily: Daily, as_of: date) -> tuple[list[float], list[float]]:
    """Average revenue and visits per weekday (Monday = 0) over the
    LOOKBACK_WEEKS weeks before as_of."""
    revenue, visits = [0.0] * 7, [0.0] * 7
    for i in range(1, LOOKBACK_WEEKS * 7 + 1):
        d = as_of - timedelta(days=i)
        r, v = daily.get(d, (0.0, 0))
        revenue[d.weekday()] += r / LOOKBACK_WEEKS
        visits[d.weekday()] += v / LOOKBACK_WEEKS
    return revenue, visits


def project(daily: Daily, as_of: date) -> dict:
    """Month-end revenue and visits as of as_of; daily holds what is known
    by then, including as_of's takings so far."""
    mean_rev, mean_vis = weekday_means(daily, as_of)
    first = as_of.replace(day=1)
    actual_rev = sum(v[0] for d, v in daily.items() if first <= d <= as_of)
    actual_vis = sum(v[1] for d, v in daily.items() if first <= d <= as_of)
    today_rev, today_vis = daily.get(as_of, (0.0, 0))
    wd = as_of.weekday()
    rest_rev, rest_vis = max(0.0, mean_rev[wd] - today_rev), max(0.0, mean_vis[wd] - today_vis)
    open_left = 1 if rest_rev > 0 else 0
    d = as_of + timedelta(days=1)
    while d <= month_end(as_of):
        rest_rev += mean_rev[d.weekday()]
        rest_vis += mean_vis[d.weekday()]
        open_left += mean_rev[d.weekday()] > 0
        d += timedelta(days=1)
    window = [daily.get(as_of - timedelta(days=i), (0.0, 0))[0] for i in range(1, LOOKBACK_WEEKS * 7 + 1)]
    open_window = [r for r in window if r > 0]
    return {
        "actualRevenue": actual_rev, "actualVisits": actual_vis,
        "restRevenue": rest_rev, "restVisits": rest_vis,
        "revenue": actual_rev + rest_rev, "visits": actual_vis + rest_vis,
        "openDaysLeft": open_left,
        "recentPerOpenDay": sum(open_window) / len(open_window) if open_window else 0.0,
    }


def customer_ratio(daily: Daily, monthly_customers: dict[str, int], first: date) -> float | None:
    """Distinct paying customers per visit over the RATIO_MONTHS months
    before the month starting at first."""
    customers = visits = 0.0
    m = first
    for _ in range(RATIO_MONTHS):
        m = prev_month(m)
        if m.strftime("%Y-%m") in monthly_customers:
            customers += monthly_customers[m.strftime("%Y-%m")]
            visits += month_totals(daily, m)[1]
    return customers / visits if visits else None


def backtest(daily: Daily, monthly_customers: dict[str, int], today: date) -> list[dict]:
    """The method replayed on past full months as of today's day of the
    month, knowing only the days before that date."""
    first_day = min(daily)
    results = []
    m = prev_month(today)
    while len(results) < BACKTEST_MONTHS:
        as_of = m.replace(day=min(today.day, month_end(m).day))
        if as_of - timedelta(weeks=LOOKBACK_WEEKS) < first_day:
            break
        p = project({d: v for d, v in daily.items() if d < as_of}, as_of)
        final_rev, _ = month_totals(daily, m)
        ratio = customer_ratio(daily, monthly_customers, m)
        final_cust = monthly_customers.get(m.strftime("%Y-%m"))
        results.append({
            "month": m.strftime("%Y-%m"), "asOf": as_of,
            "revenue": p["revenue"], "restRevenue": p["restRevenue"], "finalRevenue": final_rev,
            "customers": p["visits"] * ratio if ratio else None,
            "restCustomers": p["restVisits"] * ratio if ratio else None,
            "finalCustomers": final_cust,
        })
        m = prev_month(m)
    return results


def _range(forecast: float, rest: float, floor: float, tests: list[tuple[float, float, float]]) -> tuple[float, float, float | None]:
    """(low, high, mean miss) from backtests given as (forecast, rest, final)."""
    misses = [(final - f) / r for f, r, final in tests if r > 0]
    mape = [abs(final - f) / final for f, r, final in tests if final]
    if not misses:
        return forecast, forecast, None
    low = max(floor, forecast + min(0.0, min(misses)) * rest)
    high = max(floor, forecast + max(0.0, max(misses)) * rest)
    return low, high, sum(mape) / len(mape) if mape else None


def forecast(daily: Daily, monthly_customers: dict[str, int], today: date) -> dict | None:
    """Month-end forecast for today's month; None until there are
    LOOKBACK_WEEKS weeks of history."""
    if not daily or today - timedelta(weeks=LOOKBACK_WEEKS) < min(daily):
        return None
    first = today.replace(day=1)
    month, prev = first.strftime("%Y-%m"), prev_month(first)
    p = project(daily, today)
    tests = backtest(daily, monthly_customers, today)

    rev_low, rev_high, rev_mape = _range(
        p["revenue"], p["restRevenue"], p["actualRevenue"],
        [(t["revenue"], t["restRevenue"], t["finalRevenue"]) for t in tests])

    actual_customers = monthly_customers.get(month, 0)
    ratio = customer_ratio(daily, monthly_customers, first)
    customers = cust_low = cust_high = cust_mape = None
    if ratio:
        customers = max(actual_customers, p["visits"] * ratio)
        cust_low, cust_high, cust_mape = _range(
            customers, p["restVisits"] * ratio, actual_customers,
            [(t["customers"], t["restCustomers"], t["finalCustomers"]) for t in tests
             if t["customers"] is not None and t["finalCustomers"] is not None])

    prev_rev, _ = month_totals(daily, prev)
    prev_cust = monthly_customers.get(prev.strftime("%Y-%m"))
    needed = None
    if prev_rev and p["openDaysLeft"]:
        needed = max(0.0, prev_rev - p["actualRevenue"]) / p["openDaysLeft"]
    return {
        "month": month, "asOf": today, "daysElapsed": today.day, "daysInMonth": month_end(today).day,
        "revenue": p["revenue"], "revenueLow": rev_low, "revenueHigh": rev_high, "revenueMape": rev_mape,
        "actualRevenue": p["actualRevenue"], "restRevenue": p["restRevenue"],
        "customers": customers, "customersLow": cust_low, "customersHigh": cust_high, "customersMape": cust_mape,
        "actualCustomers": actual_customers,
        "prevMonth": prev.strftime("%Y-%m"), "prevRevenue": prev_rev, "prevCustomers": prev_cust,
        "openDaysLeft": p["openDaysLeft"], "neededPerOpenDay": needed,
        "recentPerOpenDay": p["recentPerOpenDay"], "backtests": tests,
    }
