"""Asia/Kolkata market-hours awareness for NSE/BSE.

Holiday list covers common full-day NSE trading holidays; it is intentionally
conservative and easy to extend — unknown holidays only mean a few wasted LTP
polls (adapters degrade gracefully), never wrong signals.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# (month, day) fixed-date holidays observed by NSE most years.
FIXED_HOLIDAYS = {
    (1, 26),   # Republic Day
    (8, 15),   # Independence Day
    (10, 2),   # Gandhi Jayanti
    (12, 25),  # Christmas
    (5, 1),    # Maharashtra Day
    (4, 14),   # Ambedkar Jayanti
}

# Extend with exchange-published dates (movable festivals) per year as needed.
EXTRA_HOLIDAYS: set[date] = set()


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_day(d: date | None = None) -> bool:
    d = d or now_ist().date()
    if d.weekday() >= 5:
        return False
    if (d.month, d.day) in FIXED_HOLIDAYS or d in EXTRA_HOLIDAYS:
        return False
    return True


def is_market_open(dt: datetime | None = None) -> bool:
    dt = dt or now_ist()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    dt = dt.astimezone(IST)
    if not is_trading_day(dt.date()):
        return False
    return MARKET_OPEN <= dt.time() <= MARKET_CLOSE
