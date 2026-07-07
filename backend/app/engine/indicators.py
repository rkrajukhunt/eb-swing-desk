"""Deterministic technical indicators — pure pandas/numpy, textbook formulas.

Every number shown in the UI traces back to one of these functions. Wilder
smoothing (RMA) is used for RSI, ATR and ADX, matching TA-Lib / pandas-ta /
TradingView defaults, so values are verifiable against standard charting
software.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing (a.k.a. RMA / SMMA)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = _rma(delta.clip(lower=0), period)
    loss = _rma((-delta).clip(lower=0), period)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # zero average loss → RSI is 100 by definition (not indeterminate)
    out = out.where(loss > 0, np.where(gain > 0, 100.0, 50.0))
    return out.fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return _rma(tr, period)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    """Returns (adx, +DI, -DI) with Wilder smoothing."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = atr(high, low, close, period)  # RMA of TR
    plus_di = 100 * _rma(plus_dm, period) / tr.replace(0, np.nan)
    minus_di = 100 * _rma(minus_dm, period) / tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return _rma(dx.fillna(0), period), plus_di.fillna(0), minus_di.fillna(0)


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return mid + num_std * std, mid, mid - num_std * std


def detect_rsi_divergence(close: pd.Series, rsi_s: pd.Series, lookback: int = 40) -> pd.Series:
    """Per-bar divergence flag: 'bearish' | 'bullish' | 'none'.

    Bearish: price makes a `lookback`-bar high while RSI is below its value at
    the previous price peak. Bullish: mirror at lows. A deliberately simple,
    fully deterministic definition — used as a scoring penalty, not a signal.
    """
    n = len(close)
    out = np.array(["none"] * n, dtype=object)
    c = close.values
    r = rsi_s.values
    half = lookback // 2
    for i in range(lookback, n):
        win_c = c[i - lookback: i + 1]
        if c[i] >= win_c.max() - 1e-9:  # new high now
            prev_peak = i - lookback + int(np.argmax(c[i - lookback: i - half + 1]))
            if prev_peak < i and c[i] > c[prev_peak] and r[i] < r[prev_peak] - 1e-9:
                out[i] = "bearish"
        if c[i] <= win_c.min() + 1e-9:  # new low now
            prev_trough = i - lookback + int(np.argmin(c[i - lookback: i - half + 1]))
            if prev_trough < i and c[i] < c[prev_trough] and r[i] > r[prev_trough] + 1e-9:
                out[i] = "bullish"
    return pd.Series(out, index=close.index)


def compute_indicators(
    daily: pd.DataFrame,
    weekly: pd.DataFrame,
    index_daily: pd.DataFrame | None = None,
    swing_low_lookback: int = 10,
) -> pd.DataFrame:
    """Full per-bar indicator frame used by BOTH the live scanner and the
    backtester (single source of truth).

    Weekly indicators are computed on completed weeks then shifted one week
    before mapping onto daily bars, so no daily bar ever sees its own
    (incomplete) week — no lookahead.
    """
    df = daily.copy()
    c, h, low_, v = df["close"], df["high"], df["low"], df["volume"]

    df["ema20"] = ema(c, 20)
    df["ema50"] = ema(c, 50)
    df["ema200"] = ema(c, 200)
    df["ema200_rising"] = df["ema200"] > df["ema200"].shift(10)
    df["rsi14"] = rsi(c, 14)
    df["rsi_rising"] = df["rsi14"] > df["rsi14"].shift(3)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(c)
    df["adx14"], df["plus_di"], df["minus_di"] = adx(h, low_, c, 14)
    df["atr14"] = atr(h, low_, c, 14)
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = bollinger(c, 20, 2.0)
    df["vol_avg20"] = v.rolling(20).mean()
    df["vol_ratio"] = v / df["vol_avg20"].replace(0, np.nan)
    df["turnover_avg20"] = (c * v).rolling(20).mean()
    df["high_20"] = h.shift(1).rolling(20).max()      # prior 20-day high (breakout trigger)
    df["swing_low"] = low_.shift(1).rolling(swing_low_lookback).min()
    df["high_52w"] = h.rolling(252, min_periods=60).max()
    df["low_52w"] = low_.rolling(252, min_periods=60).min()
    df["dist_52w_high_pct"] = (c / df["high_52w"] - 1) * 100
    df["rsi_divergence"] = detect_rsi_divergence(c, df["rsi14"])

    # Relative strength vs NIFTY over 55 sessions (percentage-point spread)
    if index_daily is not None and not index_daily.empty:
        idx_close = index_daily["close"].reindex(df.index).ffill()
        stock_ret = (c / c.shift(55) - 1) * 100
        index_ret = (idx_close / idx_close.shift(55) - 1) * 100
        df["rel_strength_55d"] = stock_ret - index_ret
    else:
        df["rel_strength_55d"] = 0.0

    # Weekly trend mapped onto daily bars (completed weeks only — shift(1))
    if weekly is not None and not weekly.empty and len(weekly) >= 25:
        wk = weekly.copy()
        wk["w_ema20"] = ema(wk["close"], 20)
        wk["w_ema50"] = ema(wk["close"], 50)
        wk_up = (wk["w_ema20"] > wk["w_ema50"]).shift(1)
        df["weekly_up"] = wk_up.reindex(df.index, method="ffill").fillna(False).astype(bool)
    else:
        df["weekly_up"] = False

    return df
