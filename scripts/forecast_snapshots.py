#!/usr/bin/env python3
"""Store the month-end revenue forecast of each morning in forecast_snapshots.

For every Chicago date from the first day of the month HISTORY_MONTHS months
back through today that has no row yet, fit the model on the days before it
(src/sms_campaign/forecast_models.py) and store the expected revenue of each
day to the end of its month. Past mornings are computed from the data known
then, so the first run back-fills the history the panel and the forecast's
accuracy range need; after that each run adds one row.

Run by scripts/daily_report.py before it writes the report, and by the panel
in the background when today's row is missing. Safe to run concurrently:
rows are inserted with ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sms_campaign import forecast, forecast_models  # noqa: E402
from sms_campaign.db import get_connection  # noqa: E402

# This month plus the 6 before it: the panel shows 6 months of history and
# the accuracy range replays the 6 months before the current one.
HISTORY_MONTHS = forecast.BACKTEST_MONTHS + 1

_lock = threading.Lock()


def missing_days(stored: set[date], first_data_day: date, today: date) -> list[date]:
    """Mornings from HISTORY_MONTHS months back through today without a row,
    skipping those with less than LOOKBACK_WEEKS weeks of data before them."""
    start = today.replace(day=1)
    for _ in range(HISTORY_MONTHS - 1):
        start = forecast.prev_month(start)
    start = max(start, first_data_day + timedelta(weeks=forecast.LOOKBACK_WEEKS))
    days, d = [], start
    while d <= today:
        if d not in stored:
            days.append(d)
        d += timedelta(days=1)
    return days


def ensure(today: date | None = None, log=print) -> int:
    """Compute and store every missing morning; returns how many were added.
    Only one run at a time per process."""
    import live_dashboard  # the same daily totals the panel uses

    if not _lock.acquire(blocking=False):
        return 0
    try:
        today = today or datetime.now(ZoneInfo("America/Chicago")).date()
        # Short connections only: a fit takes seconds, and the Supabase pooler
        # drops prepared statements on connections held across them.
        conn = get_connection()
        try:
            daily = {r["day"]: float(r["revenue"] or 0) for r in live_dashboard.fetch_daily_rows(conn)}
            stored = {r["as_of"] for r in conn.execute("SELECT as_of FROM forecast_snapshots").fetchall()}
        finally:
            conn.close()
        if not daily:
            return 0
        todo = missing_days(stored, min(daily), today)
        for as_of in todo:
            known = {d: v for d, v in daily.items() if d < as_of}
            days = forecast_models.predict_days(known, as_of, forecast.month_end(as_of))
            actual = sum(v for d, v in known.items() if d >= as_of.replace(day=1))
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT INTO forecast_snapshots (as_of, model, actual_before, days_json) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (as_of) DO NOTHING",
                    (as_of, forecast_models.MODEL_NAME, actual, json.dumps([round(v, 2) for v in days])),
                )
                conn.commit()
            finally:
                conn.close()
            log(f"forecast snapshot {as_of}: ${actual + sum(days):,.0f}")
        return len(todo)
    finally:
        _lock.release()


def ensure_in_background() -> None:
    """Start ensure() in a daemon thread unless one is already running."""
    if _lock.locked():
        return

    def run():
        try:
            ensure(log=lambda _msg: None)
        except Exception as exc:  # noqa: BLE001
            print(f"forecast snapshots failed: {exc}", file=sys.stderr)

    threading.Thread(target=run, daemon=True).start()


def load(conn) -> dict[date, dict]:
    """Stored mornings as {as_of: {"actualBefore": float, "days": [float]}};
    empty if the table doesn't exist yet."""
    import psycopg

    try:
        with conn.transaction():
            rows = conn.execute("SELECT as_of, actual_before, days_json FROM forecast_snapshots").fetchall()
    except psycopg.Error:
        return {}
    return {r["as_of"]: {"actualBefore": float(r["actual_before"]), "days": json.loads(r["days_json"])}
            for r in rows}


if __name__ == "__main__":
    added = ensure()
    print(f"{added} forecast snapshot(s) added")
