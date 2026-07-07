"""Composite score (0–100): weighted blend of trend, momentum, volume
confirmation, relative strength and R:R quality, with transparent per-factor
sub-scores and explicit penalties for conflicting signals.
"""
from __future__ import annotations

import pandas as pd

WEIGHTS = {
    "trend": 0.25,
    "momentum": 0.25,
    "volume": 0.15,
    "rel_strength": 0.20,
    "rr_quality": 0.15,
}


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _lin(x: float, x0: float, x1: float) -> float:
    """Map x linearly from [x0, x1] → [0, 100], clamped."""
    if x1 == x0:
        return 50.0
    return _clamp((x - x0) / (x1 - x0) * 100.0)


def score_candidate(row: pd.Series, eff_rr: float) -> tuple[float, dict, list[str]]:
    """Returns (composite 0-100, sub_scores, penalty descriptions)."""
    close = float(row["close"])
    e20, e50, e200 = float(row["ema20"]), float(row["ema50"]), float(row["ema200"])

    # Trend: EMA stack + price above EMA200 + rising EMA200
    trend = 0.0
    trend += 40.0 if close > e200 else 0.0
    trend += 30.0 if e20 > e50 else 0.0
    trend += 15.0 if bool(row["ema200_rising"]) else 0.0
    trend += _lin(float(row["adx14"]), 15, 40) * 0.15

    # Momentum: RSI in the healthy zone + positive MACD histogram
    r = float(row["rsi14"])
    rsi_score = _lin(r, 35, 65) if r <= 65 else _lin(80 - (r - 65), 35, 65)
    macd_score = 100.0 if float(row["macd_hist"]) > 0 else 30.0
    momentum = 0.6 * rsi_score + 0.4 * macd_score

    # Volume confirmation: today's volume vs 20d average
    volume = _lin(float(row["vol_ratio"]) if pd.notna(row["vol_ratio"]) else 1.0, 0.8, 2.5)

    # Relative strength vs NIFTY, 55 sessions (pp spread; +10pp → max score)
    rel = _lin(float(row["rel_strength_55d"]), -5.0, 10.0)

    # R:R quality from signal math
    rr_q = _lin(eff_rr, 1.0, 3.0)

    subs = {
        "trend": round(trend, 1),
        "momentum": round(momentum, 1),
        "volume": round(volume, 1),
        "rel_strength": round(rel, 1),
        "rr_quality": round(rr_q, 1),
    }
    composite = sum(subs[k] * WEIGHTS[k] for k in WEIGHTS)

    penalties: list[str] = []
    if str(row["rsi_divergence"]) == "bearish":
        composite -= 15
        penalties.append("bearish RSI divergence (−15)")
    if not bool(row["weekly_up"]):
        composite -= 10
        penalties.append("weekly trend not up (−10)")
    if e20 > 0 and close / e20 > 1.10:
        composite -= 10
        penalties.append("extended >10% above EMA20 (−10)")

    return round(_clamp(composite), 1), subs, penalties
