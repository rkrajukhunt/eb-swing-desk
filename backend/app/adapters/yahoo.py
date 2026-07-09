"""Yahoo Finance adapter — live NSE data, no credentials.

Daily candles and last-traded prices come from Yahoo Finance's public chart
endpoint (symbols suffixed `.NS`, index `^NSEI`). No API key, no login.

Honesty rule: in production this NEVER returns synthetic prices. If Yahoo is
unreachable it returns nothing for that symbol, so a fabricated number can never
be shown as a live quote. A deterministic synthetic generator exists ONLY for
the offline test suite (gated on `pytest` being imported) so tests stay fast and
network-free — it is never reachable from a running server.

Option chains: Yahoo has no free NIFTY option feed, so option quotes are
Black-Scholes *theoretical* prices computed over the live spot (clearly a model,
not a market quote).
"""
from __future__ import annotations

import hashlib
import logging
import math
import sys
from datetime import datetime, timedelta

import numpy as np

from .base import BrokerAdapter, BrokerStatus, Candle, Instrument
from ..services.universe import get_universe_symbols

log = logging.getLogger(__name__)

INDEX_SYMBOL = "NIFTY 50"
_DETAIL = "Yahoo Finance — live NSE data (no API key)"


def _under_pytest() -> bool:
    """Synthetic data is reachable only from the test suite, never a live server."""
    return "pytest" in sys.modules


def _yahoo_symbol(symbol: str) -> str:
    return "^NSEI" if symbol in (INDEX_SYMBOL, "NIFTY 50") else f"{symbol}.NS"


# --- deterministic synthetic generator — TEST-ONLY ----------------------------
def _seed_for(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


def _base_price(symbol: str) -> float:
    h = _seed_for(symbol) % 1000
    return 60.0 + (h / 999.0) ** 2 * 3940.0


def _synthetic_series(symbol: str, n_days: int) -> list[Candle]:
    """Reproducible seeded random-walk with regime phases so strategies find
    setups offline. Used ONLY under pytest — see module docstring."""
    rng = np.random.default_rng(_seed_for(symbol))
    base = _base_price(symbol) if symbol != INDEX_SYMBOL else 18000.0

    n_phases = 14
    phase_len = n_days // n_phases + 1
    drifts = rng.choice([0.0012, 0.0006, 0.0, -0.0006, -0.0012], size=n_phases,
                        p=[0.25, 0.25, 0.2, 0.15, 0.15])
    drift = np.repeat(drifts, phase_len)[:n_days]
    vol = 0.016 if symbol != INDEX_SYMBOL else 0.009
    rets = drift + rng.normal(0, vol, n_days)
    closes = base * np.exp(np.cumsum(rets))

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
    for ts, c in zip(dates, closes):
        o = prev_close * (1 + rng.normal(0, 0.003))
        hi = max(o, c) * (1 + abs(rng.normal(0, 0.006)))
        lo = min(o, c) * (1 - abs(rng.normal(0, 0.006)))
        move = abs(c / prev_close - 1)
        v = base_vol * (1 + rng.normal(0, 0.3) + 25 * move)
        candles.append(Candle(ts=ts, open=round(o, 2), high=round(hi, 2),
                              low=round(lo, 2), close=round(c, 2),
                              volume=max(1000.0, round(v))))
        prev_close = c
    return candles


class YahooAdapter(BrokerAdapter):
    name = "yahoo"

    def __init__(self) -> None:
        self._cache: dict[str, list[Candle]] = {}

    def authenticate(self, **kwargs) -> BrokerStatus:
        return BrokerStatus(name=self.name, authenticated=True, detail=_DETAIL)

    def status(self) -> BrokerStatus:
        return BrokerStatus(name=self.name, authenticated=True, detail=_DETAIL)

    # ------------------------------------------------------------------
    def _series(self, symbol: str, n_days: int = 1600) -> list[Candle]:
        key = f"{symbol}:{n_days}"
        if key in self._cache:
            return self._cache[key]

        if _under_pytest():
            candles = _synthetic_series(symbol, n_days)
            self._cache[key] = candles
            return candles

        # Production: real Yahoo data, or nothing. Never synthetic.
        try:
            import httpx
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
                   f"{_yahoo_symbol(symbol)}?range=10y&interval=1d")
            r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10.0)
            if r.status_code == 200:
                res = (r.json().get("chart", {}).get("result") or [{}])[0]
                ts_list = res.get("timestamp", []) or []
                q = (res.get("indicators", {}).get("quote") or [{}])[0]
                candles = []
                for t, o, h, low_, c, v in zip(ts_list, q.get("open", []), q.get("high", []),
                                               q.get("low", []), q.get("close", []),
                                               q.get("volume", [])):
                    if None in (o, h, low_, c):
                        continue
                    candles.append(Candle(
                        ts=datetime.fromtimestamp(t),
                        open=round(float(o), 2), high=round(float(h), 2),
                        low=round(float(low_), 2), close=round(float(c), 2),
                        volume=max(1000.0, round(float(v or 0))),
                    ))
                if candles:
                    candles = candles[-n_days:]
                    self._cache[key] = candles
                    return candles
        except Exception as e:
            log.warning("Yahoo history fetch failed for %s: %s", symbol, e)
        # Honest failure: no data rather than a fabricated series.
        return []

    # ------------------------------------------------------------------
    def get_historical_ohlc(self, symbol, interval, from_dt, to_dt) -> list[Candle]:
        daily = [c for c in self._series(symbol) if from_dt <= c.ts <= to_dt]
        if interval == "day":
            return daily
        if interval == "week":
            return _resample_weekly(daily)
        raise ValueError(f"unsupported interval {interval}")

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        if _under_pytest():
            return {s: p for s in symbols if (p := self._synthetic_ltp(s)) is not None}

        # Production: real Yahoo quotes only. A symbol Yahoo can't price is omitted
        # (callers already tolerate missing symbols) — never a synthetic stand-in.
        import httpx
        from concurrent.futures import ThreadPoolExecutor

        def fetch_one(s: str) -> tuple[str, float | None]:
            try:
                url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
                       f"{_yahoo_symbol(s)}?range=1d")
                r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5.0)
                if r.status_code == 200:
                    meta = (r.json().get("chart", {}).get("result") or [{}])[0].get("meta", {})
                    price = meta.get("regularMarketPrice")
                    if price is not None:
                        return s, round(float(price), 2)
            except Exception:
                pass
            return s, None

        out: dict[str, float] = {}
        if not symbols:
            return out
        with ThreadPoolExecutor(max_workers=min(10, len(symbols))) as ex:
            for s, price in ex.map(fetch_one, symbols):
                if price is not None:
                    out[s] = price
        return out

    def _synthetic_ltp(self, symbol: str) -> float | None:
        """TEST-ONLY last price: last synthetic close with a small intraday wiggle."""
        series = self._series(symbol)
        if not series:
            return None
        last = series[-1].close
        wiggle = math.sin(datetime.now().minute * 6.28 / 60 + (_seed_for(symbol) % 10)) * 0.004
        return round(last * (1 + wiggle), 2)

    def get_instruments(self) -> list[Instrument]:
        return [Instrument(symbol=s) for s in get_universe_symbols("NIFTY500")]

    # --- Options: Black-Scholes THEORETICAL chain over the live spot ----------
    # Yahoo has no free NIFTY option feed; these are model prices, not market quotes.
    def _bs_iv(self, spot: float, strike: int, opt_type: str) -> float:
        """Deterministic vol smile: ~13% ATM, rising in the wings, put skew,
        plus a small time-of-day wiggle so paper MTM moves between polls."""
        moneyness = abs(strike - spot) / spot
        iv = 0.13 + 0.55 * moneyness
        if opt_type == "PE" and strike < spot:
            iv += 0.015  # index put skew
        iv += 0.004 * math.sin(datetime.now().minute * 6.28 / 60 + strike % 7)
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
        spot = self.get_ltp([underlying]).get(underlying)
        if spot is None:
            return {}
        t = max(dte_calendar_days(expiry), 0.02) / 365.0
        r = float(opt["risk_free_rate_pct"]) / 100.0
        out: dict[str, float] = {}
        for strike, opt_type in items:
            iv = self._bs_iv(spot, strike, opt_type)
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
