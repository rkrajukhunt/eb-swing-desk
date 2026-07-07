"""OHLC caching layer.

Historical candles are cached in the DB; the broker is only hit for the
missing tail. Weekly data is always derived from cached daily data so both
timeframes stay consistent and rate limits are respected.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import delete, select

from ..adapters.base import BrokerAdapter, BrokerError
from ..database import db_session
from ..models import OhlcCandle

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 365 * 6  # enough history for EMA200 weekly + multi-year backtests


def load_daily(symbol: str) -> pd.DataFrame:
    """Cached daily candles as a DataFrame indexed by timestamp (ascending)."""
    with db_session() as s:
        rows = s.execute(
            select(OhlcCandle)
            .where(OhlcCandle.symbol == symbol, OhlcCandle.interval == "day")
            .order_by(OhlcCandle.ts)
        ).scalars().all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(
        {
            "ts": [r.ts for r in rows],
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        }
    ).set_index("ts")
    return df[~df.index.duplicated(keep="last")]


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily → calendar-week candles (week ends Friday)."""
    if daily.empty:
        return daily
    return daily.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])


def refresh_symbol(adapter: BrokerAdapter, symbol: str) -> int:
    """Fetch only the missing tail of daily candles for `symbol`; upsert into cache.

    Returns the number of candles written. Raises BrokerError upward only on
    total failure; partial data is kept.
    """
    existing = load_daily(symbol)
    now = datetime.now()
    if existing.empty:
        from_dt = now - timedelta(days=LOOKBACK_DAYS)
    else:
        last = existing.index.max().to_pydatetime()
        if (now - last).days < 1:
            return 0
        from_dt = last - timedelta(days=5)  # small overlap; upsert dedupes

    candles = adapter.get_historical_ohlc(symbol, "day", from_dt, now)
    if not candles:
        return 0
    with db_session() as s:
        tss = [c.ts for c in candles]
        s.execute(
            delete(OhlcCandle).where(
                OhlcCandle.symbol == symbol,
                OhlcCandle.interval == "day",
                OhlcCandle.ts.in_(tss),
            )
        )
        s.add_all(
            OhlcCandle(symbol=symbol, interval="day", ts=c.ts, open=c.open,
                       high=c.high, low=c.low, close=c.close, volume=c.volume)
            for c in candles
        )
    return len(candles)


def refresh_universe(adapter: BrokerAdapter, symbols: list[str]) -> dict:
    """Refresh many symbols, tolerating per-symbol failures (holidays, halts,
    missing tokens). Returns a summary the UI can show."""
    ok, failed = 0, []
    for sym in symbols:
        try:
            refresh_symbol(adapter, sym)
            ok += 1
        except BrokerError as e:
            log.warning("refresh failed for %s: %s", sym, e)
            failed.append({"symbol": sym, "error": str(e)})
        except Exception as e:  # never let one bad symbol kill a scan
            log.exception("unexpected refresh error for %s", sym)
            failed.append({"symbol": sym, "error": str(e)})
    return {"refreshed": ok, "failed": failed}
