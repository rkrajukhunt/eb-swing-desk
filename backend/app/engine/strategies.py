"""Config-driven strategy presets — the SINGLE implementation used by both the
live scanner and the backtester (backtest == live logic, by construction).

Each evaluator takes one indicator row (a bar) plus its strategy params and
returns a StrategyMatch with a human-auditable list of passed conditions, or
None. No prices are invented here; entry/SL/target math lives in signals.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

STRATEGY_LABELS = {
    "trend_pullback": "Trend Pullback",
    "breakout": "Breakout",
    "mean_reversion": "Mean Reversion",
}


@dataclass
class StrategyMatch:
    strategy: str
    reasons: list[str] = field(default_factory=list)


def _f(row: pd.Series, key: str) -> float:
    v = row[key]
    return float(v) if pd.notna(v) else float("nan")


# ---------------------------------------------------------------------------
def eval_trend_pullback(row: pd.Series, p: dict) -> StrategyMatch | None:
    reasons = []
    close, e20, e50, e200 = _f(row, "close"), _f(row, "ema20"), _f(row, "ema50"), _f(row, "ema200")
    if not close > e200:
        return None
    reasons.append(f"close {close:.2f} > EMA200 {e200:.2f} (long-term uptrend)")
    if not e20 > e50:
        return None
    reasons.append(f"EMA20 {e20:.2f} > EMA50 {e50:.2f} (medium-term up)")

    r = _f(row, "rsi14")
    if not (p["rsi_min"] <= r <= p["rsi_max"]):
        return None
    if p.get("require_rsi_rising", True) and not bool(row["rsi_rising"]):
        return None
    reasons.append(f"RSI14 {r:.1f} in [{p['rsi_min']}, {p['rsi_max']}] and rising")

    # Pullback: close near EMA20 or EMA50 support
    dist20 = abs(close / e20 - 1) * 100
    dist50 = abs(close / e50 - 1) * 100
    max_dist = float(p["pullback_max_dist_ema20_pct"])
    if min(dist20, dist50) > max_dist:
        return None
    reasons.append(f"pullback: close within {min(dist20, dist50):.2f}% of EMA20/50 (max {max_dist}%)")

    a = _f(row, "adx14")
    if not a > p["adx_min"]:
        return None
    reasons.append(f"ADX14 {a:.1f} > {p['adx_min']} (trending)")

    rs = _f(row, "rel_strength_55d")
    if not rs >= p["rs_min"]:
        return None
    reasons.append(f"RS vs NIFTY (55d) {rs:+.1f}pp ≥ {p['rs_min']} (outperformer)")

    if not bool(row["weekly_up"]):
        return None
    reasons.append("weekly EMA20 > EMA50 (multi-timeframe confirmation)")
    return StrategyMatch("trend_pullback", reasons)


def eval_breakout(row: pd.Series, p: dict) -> StrategyMatch | None:
    reasons = []
    close, h20 = _f(row, "close"), _f(row, "high_20")
    if pd.isna(h20) or not close > h20:
        return None
    reasons.append(f"close {close:.2f} breaks {int(p['breakout_lookback'])}-day high {h20:.2f}")

    vr = _f(row, "vol_ratio")
    if not vr >= p["vol_mult_min"]:
        return None
    reasons.append(f"volume {vr:.2f}x 20d avg ≥ {p['vol_mult_min']}x (confirmed)")

    a = _f(row, "adx14")
    if not a > p["adx_min"]:
        return None
    reasons.append(f"ADX14 {a:.1f} > {p['adx_min']}")

    r = _f(row, "rsi14")
    if not (p["rsi_min"] <= r <= p["rsi_max"]):
        return None
    reasons.append(f"RSI14 {r:.1f} in [{p['rsi_min']}, {p['rsi_max']}] (strong, not blow-off)")

    rs = _f(row, "rel_strength_55d")
    if not rs >= p["rs_min"]:
        return None
    reasons.append(f"RS vs NIFTY (55d) {rs:+.1f}pp ≥ {p['rs_min']}")

    if not bool(row["weekly_up"]):
        return None
    reasons.append("weekly EMA20 > EMA50 (multi-timeframe confirmation)")
    return StrategyMatch("breakout", reasons)


def eval_mean_reversion(row: pd.Series, p: dict) -> StrategyMatch | None:
    reasons = []
    r = _f(row, "rsi14")
    if not r < p["rsi_max"]:
        return None
    reasons.append(f"RSI14 {r:.1f} < {p['rsi_max']} (oversold)")

    close, bbl = _f(row, "close"), _f(row, "bb_lower")
    if p.get("require_lower_bb", True) and not (pd.notna(bbl) and close <= bbl * 1.005):
        return None
    reasons.append(f"close {close:.2f} at/below lower Bollinger {bbl:.2f}")

    if p.get("require_ema200_rising", True):
        if not (bool(row["ema200_rising"]) and close > _f(row, "ema200") * 0.97):
            return None
        reasons.append("EMA200 rising and price near/above it (dip in an uptrend, not a crash)")
    return StrategyMatch("mean_reversion", reasons)


_EVALUATORS = {
    "trend_pullback": eval_trend_pullback,
    "breakout": eval_breakout,
    "mean_reversion": eval_mean_reversion,
}


def evaluate_strategies(
    row: pd.Series,
    strategies_cfg: dict,
    regime_allows_longs: bool,
    only: str | None = None,
) -> list[StrategyMatch]:
    """Evaluate all enabled presets on one bar.

    Regime gate: trend_pullback and breakout (aggressive longs) are suppressed
    unless the index regime is bullish/neutral. Mean reversion already demands
    a rising EMA200 on the stock itself.
    """
    matches: list[StrategyMatch] = []
    for name, params in strategies_cfg.items():
        if only and name != only:
            continue
        if not params.get("enabled", True):
            continue
        if name in ("trend_pullback", "breakout") and not regime_allows_longs:
            continue
        fn = _EVALUATORS.get(name)
        if fn is None:
            continue
        m = fn(row, params)
        if m:
            matches.append(m)
    return matches
