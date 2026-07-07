"""Market regime filter — the gatekeeper.

NIFTY 50 trend classified from price vs EMA200, EMA50 slope and ADX/DI:
  bullish  — price > EMA200 and (+DI > -DI or EMA50 rising)
  bearish  — price < EMA200 and ADX > 20 and -DI > +DI (confirmed downtrend)
  neutral  — everything else
Long breakout/trend signals are only taken in bullish/neutral regimes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .indicators import adx, ema


@dataclass
class Regime:
    label: str = "unknown"          # bullish | neutral | bearish | unknown
    detail: dict = field(default_factory=dict)

    @property
    def allows_longs(self) -> bool:
        return self.label in ("bullish", "neutral")


def classify_regime(index_daily: pd.DataFrame) -> Regime:
    if index_daily is None or len(index_daily) < 210:
        return Regime("unknown", {"reason": "insufficient index history"})

    c, h, low_ = index_daily["close"], index_daily["high"], index_daily["low"]
    e200 = ema(c, 200)
    e50 = ema(c, 50)
    adx_s, pdi, mdi = adx(h, low_, c, 14)

    close = float(c.iloc[-1])
    above_200 = close > float(e200.iloc[-1])
    e50_rising = float(e50.iloc[-1]) > float(e50.iloc[-11])
    a = float(adx_s.iloc[-1])
    p, m = float(pdi.iloc[-1]), float(mdi.iloc[-1])

    if above_200 and (p > m or e50_rising):
        label = "bullish"
    elif (not above_200) and a > 20 and m > p:
        label = "bearish"
    else:
        label = "neutral"

    return Regime(label, {
        "close": round(close, 2),
        "ema200": round(float(e200.iloc[-1]), 2),
        "above_ema200": above_200,
        "ema50_rising": e50_rising,
        "adx": round(a, 1),
        "plus_di": round(p, 1),
        "minus_di": round(m, 1),
    })
