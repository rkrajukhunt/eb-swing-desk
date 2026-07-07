"""Deterministic synthetic-data adapter.

Lets the whole system (scans, paper trading, backtests, UI) run end-to-end
with zero broker credentials. Each symbol gets a reproducible seeded
random-walk with regime phases so strategies actually find setups. The NIFTY
50 index series is generated the same way under the symbol "NIFTY 50".
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta

import numpy as np

from .base import BrokerAdapter, BrokerStatus, Candle, Instrument
from ..services.universe import get_universe_symbols

INDEX_SYMBOL = "NIFTY 50"


def _seed_for(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


def _base_price(symbol: str) -> float:
    # Spread base prices between ~60 and ~4000 deterministically
    h = _seed_for(symbol) % 1000
    return 60.0 + (h / 999.0) ** 2 * 3940.0


class MockAdapter(BrokerAdapter):
    name = "mock"

    def __init__(self) -> None:
        self._cache: dict[str, list[Candle]] = {}

    def authenticate(self, **kwargs) -> BrokerStatus:
        return BrokerStatus(name=self.name, authenticated=True, detail="Mock broker — synthetic data")

    def status(self) -> BrokerStatus:
        return BrokerStatus(name=self.name, authenticated=True, detail="Mock broker — synthetic data")

    # ------------------------------------------------------------------
    def _series(self, symbol: str, n_days: int = 1600) -> list[Candle]:
        key = f"{symbol}:{n_days}"
        if key in self._cache:
            return self._cache[key]

        rng = np.random.default_rng(_seed_for(symbol))
        base = _base_price(symbol) if symbol != INDEX_SYMBOL else 18000.0

        # Regime phases: alternating drifts to create trends, pullbacks, ranges
        n_phases = 14
        phase_len = n_days // n_phases + 1
        drifts = rng.choice([0.0012, 0.0006, 0.0, -0.0006, -0.0012], size=n_phases,
                            p=[0.25, 0.25, 0.2, 0.15, 0.15])
        drift = np.repeat(drifts, phase_len)[:n_days]
        vol = 0.016 if symbol != INDEX_SYMBOL else 0.009
        rets = drift + rng.normal(0, vol, n_days)
        closes = base * np.exp(np.cumsum(rets))

        # Build daily candles on business days ending today
        end = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        dates: list[datetime] = []
        d = end
        while len(dates) < n_days:
            if d.weekday() < 5:
                dates.append(d)
            d -= timedelta(days=1)
        dates.reverse()

        base_vol = 200_000 + (_seed_for(symbol) % 3_000_000)
        candles: list[Candle] = []
        prev_close = closes[0]
        for i, (ts, c) in enumerate(zip(dates, closes)):
            o = prev_close * (1 + rng.normal(0, 0.003))
            hi = max(o, c) * (1 + abs(rng.normal(0, 0.006)))
            lo = min(o, c) * (1 - abs(rng.normal(0, 0.006)))
            # volume spikes on big moves — lets breakout volume filters fire
            move = abs(c / prev_close - 1)
            v = base_vol * (1 + rng.normal(0, 0.3) + 25 * move)
            candles.append(Candle(ts=ts, open=round(o, 2), high=round(hi, 2),
                                  low=round(lo, 2), close=round(c, 2),
                                  volume=max(1000.0, round(v))))
            prev_close = c
        self._cache[key] = candles
        return candles

    # ------------------------------------------------------------------
    def get_historical_ohlc(self, symbol, interval, from_dt, to_dt) -> list[Candle]:
        daily = [c for c in self._series(symbol) if from_dt <= c.ts <= to_dt]
        if interval == "day":
            return daily
        if interval == "week":
            return _resample_weekly(daily)
        raise ValueError(f"unsupported interval {interval}")

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for s in symbols:
            series = self._series(s)
            last = series[-1].close
            # small deterministic intraday wiggle so P&L moves between polls
            t = datetime.now()
            wiggle = math.sin(t.minute * 6.28 / 60 + (_seed_for(s) % 10)) * 0.004
            out[s] = round(last * (1 + wiggle), 2)
        return out

    def get_instruments(self) -> list[Instrument]:
        return [Instrument(symbol=s) for s in get_universe_symbols("NIFTY500")]

    # --- Options: synthetic Black-Scholes chain over the mock spot -----------
    def _mock_iv(self, spot: float, strike: int, opt_type: str) -> float:
        """Deterministic vol smile: ~13% ATM, rising in the wings, put skew,
        plus a small time-of-day wiggle so paper MTM moves between polls."""
        moneyness = abs(strike - spot) / spot
        iv = 0.13 + 0.55 * moneyness
        if opt_type == "PE" and strike < spot:
            iv += 0.015  # index put skew
        t = datetime.now()
        iv += 0.004 * math.sin(t.minute * 6.28 / 60 + strike % 7)
        return max(iv, 0.08)

    def get_option_expiries(self, underlying: str):
        from ..options.expiries import next_weekly_expiries
        from ..services.settings_store import get_settings

        return next_weekly_expiries(int(get_settings()["options"]["expiry_weekday"]))

    def get_option_quotes(self, underlying, expiry, items):
        from ..options.expiries import dte_calendar_days
        from ..options.pricing import bs_price
        from ..services.settings_store import get_settings

        opt = get_settings()["options"]
        spot = self.get_ltp([underlying])[underlying]
        t = max(dte_calendar_days(expiry), 0.02) / 365.0
        r = float(opt["risk_free_rate_pct"]) / 100.0
        out: dict[str, float] = {}
        for strike, opt_type in items:
            iv = self._mock_iv(spot, strike, opt_type)
            price = bs_price(spot, float(strike), t, iv, r, opt_type)
            out[f"{strike}{opt_type}"] = round(max(price, 0.05), 2)
        return out


def _resample_weekly(daily: list[Candle]) -> list[Candle]:
    weeks: dict[tuple, list[Candle]] = {}
    for c in daily:
        key = c.ts.isocalendar()[:2]
        weeks.setdefault(key, []).append(c)
    out = []
    for _, cs in sorted(weeks.items()):
        out.append(Candle(
            ts=cs[-1].ts,
            open=cs[0].open,
            high=max(c.high for c in cs),
            low=min(c.low for c in cs),
            close=cs[-1].close,
            volume=sum(c.volume for c in cs),
        ))
    return out
