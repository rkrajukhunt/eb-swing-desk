"""Weekly expiry calendar for index options (config-driven weekday, IST)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..utils.market_hours import IST, is_trading_day

EXPIRY_CUTOFF = time(15, 30)


def _prev_trading_day(d: date) -> date:
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def next_weekly_expiries(weekday: int, count: int = 4, now: datetime | None = None) -> list[date]:
    """Next `count` weekly expiry dates. If the nominal expiry weekday is a
    holiday, the exchange moves expiry to the previous trading day."""
    now = now or datetime.now(IST)
    today = now.date()
    out: list[date] = []
    d = today
    while len(out) < count:
        days_ahead = (weekday - d.weekday()) % 7
        nominal = d + timedelta(days=days_ahead)
        actual = _prev_trading_day(nominal)
        expired = actual < today or (
            actual == today and now.time() > EXPIRY_CUTOFF
        )
        if not expired and (not out or actual > out[-1]):
            out.append(actual)
        d = nominal + timedelta(days=1)
    return out


def dte_calendar_days(expiry: date, now: datetime | None = None) -> float:
    """Calendar days to expiry cutoff (fractional). Floor of 0."""
    now = now or datetime.now(IST)
    cutoff = datetime.combine(expiry, EXPIRY_CUTOFF, tzinfo=IST)
    return max((cutoff - now).total_seconds() / 86400.0, 0.0)
