"""Daily revenue models behind the month-end forecast: the average of an ETS
(Holt-Winters) model and a Prophet model, each fitted on every day before the
forecast morning (closed days count as $0).

Chosen by backtest (2026-11-16): on every morning of Jan-Oct 2026, fitting on
the days before it only, the average missed the month's final revenue by 5.1%
on average (8.2% in the first week), against 7.0% (12.2%) for the 4-week
weekday means in forecast.py. ETS alone forecasts low and Prophet alone high
when the trend turns; their average cancels most of that.

statsmodels and prophet are imported lazily: a fit takes a few seconds, so
the results are stored once per morning (scripts/forecast_snapshots.py) and
the panel only reads them.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta

MODEL_NAME = "ets+prophet"

# ETS: additive damped trend and additive weekly seasonality.
ETS_TREND = "add"
ETS_DAMPED = True
ETS_WINDOW_DAYS: int | None = None  # None = all history
# Prophet: weekly seasonality and a piecewise linear trend; one year of data
# is too little for yearly seasonality.
PROPHET_CHANGEPOINT_PRIOR = 0.05
PROPHET_SEASONALITY_MODE = "additive"
PROPHET_WINDOW_DAYS: int | None = None
PROPHET_US_HOLIDAYS = False


def _history(daily: dict[date, float], as_of: date) -> list[tuple[date, float]]:
    """Every day from the first record to the day before as_of; days with no
    payments are $0."""
    first = min(daily)
    out, d = [], first
    while d < as_of:
        out.append((d, daily.get(d, 0.0)))
        d += timedelta(days=1)
    return out


def _ets(values: list[float], n: int) -> list[float]:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    y = values[-ETS_WINDOW_DAYS:] if ETS_WINDOW_DAYS else values
    fit = ExponentialSmoothing(y, trend=ETS_TREND, damped_trend=ETS_DAMPED if ETS_TREND else False,
                               seasonal="add", seasonal_periods=7).fit()
    return [float(v) for v in fit.forecast(n)]


def _prophet(hist: list[tuple[date, float]], days: list[date]) -> list[float]:
    import pandas as pd
    from prophet import Prophet

    for name in ("cmdstanpy", "prophet"):
        logging.getLogger(name).setLevel(logging.ERROR)
    h = hist[-PROPHET_WINDOW_DAYS:] if PROPHET_WINDOW_DAYS else hist
    m = Prophet(weekly_seasonality=True, yearly_seasonality=False, daily_seasonality=False,
                changepoint_prior_scale=PROPHET_CHANGEPOINT_PRIOR, seasonality_mode=PROPHET_SEASONALITY_MODE)
    if PROPHET_US_HOLIDAYS:
        m.add_country_holidays(country_name="US")
    m.fit(pd.DataFrame({"ds": [d for d, _ in h], "y": [v for _, v in h]}))
    return [float(v) for v in m.predict(pd.DataFrame({"ds": days}))["yhat"]]


def predict_days(daily: dict[date, float], as_of: date, last: date) -> list[float]:
    """Expected revenue for each day from as_of to last, knowing only the days
    before as_of: the average of ETS and Prophet, never below $0."""
    hist = _history(daily, as_of)
    days = [as_of + timedelta(days=i) for i in range((last - as_of).days + 1)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ets = _ets([v for _, v in hist], len(days))
        pro = _prophet(hist, days)
    return [max(0.0, (a + b) / 2) for a, b in zip(ets, pro)]
