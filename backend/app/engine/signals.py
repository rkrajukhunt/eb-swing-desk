"""Hard signal math: entry, stop-loss, targets, R:R, position size.

Pure functions of indicator values and settings. The LLM never touches these
numbers; the UI and paper engine always read the output of this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import floor

import pandas as pd


@dataclass
class SignalLevels:
    entry: float
    stop_loss: float
    target_2r: float
    target_3r: float
    risk_reward: float          # effective/headroom R:R used for the quality gate
    pct_risk: float             # SL distance as % of entry
    suggested_qty: int
    reasons: list[str] = field(default_factory=list)


def build_levels(row: pd.Series, strategy: str, settings: dict) -> tuple[SignalLevels | None, str]:
    """Returns (levels, reject_reason). levels is None when the setup is rejected."""
    entry = float(row["close"])
    atr_v = float(row["atr14"])
    swing_low = float(row["swing_low"]) if pd.notna(row["swing_low"]) else float("nan")
    if pd.isna(atr_v) or atr_v <= 0:
        return None, "ATR unavailable"

    atr_stop = entry - atr_v * float(settings["atr_stop_mult"])
    # max() picks the HIGHER (tighter/safer) of ATR stop vs recent swing low
    stop = max(atr_stop, swing_low) if pd.notna(swing_low) else atr_stop
    reasons = [
        f"SL = max(entry − {settings['atr_stop_mult']}×ATR = {atr_stop:.2f}, "
        f"swing low({settings['swing_low_lookback']}) = {swing_low:.2f}) → {stop:.2f}"
    ]

    risk = entry - stop
    if risk <= 0:
        return None, "non-positive risk (SL above entry)"
    pct_risk = risk / entry * 100
    if pct_risk > float(settings["max_risk_pct_of_price"]):
        return None, f"risk {pct_risk:.1f}% of price > max {settings['max_risk_pct_of_price']}%"

    target_2r = entry + 2 * risk
    target_3r = entry + 3 * risk
    reasons.append(f"targets: entry + 2R = {target_2r:.2f}, entry + 3R = {target_3r:.2f} (R = {risk:.2f})")

    # R:R quality gate — reward must be plausibly reachable. For non-breakout
    # setups the 52-week high is the nearest structural ceiling; a breakout is
    # already at new highs so headroom is open.
    min_rr = float(settings["min_rr"])
    if strategy == "breakout":
        eff_rr = 3.0
        reasons.append("breakout at new highs → open headroom, effective R:R capped at 3.0")
    else:
        high_52w = float(row["high_52w"]) if pd.notna(row["high_52w"]) else entry * 1.5
        headroom = max(high_52w - entry, 0.0)
        eff_rr = min(headroom / risk, 3.0)
        reasons.append(
            f"headroom to 52w high {high_52w:.2f} = {headroom:.2f} → effective R:R {eff_rr:.2f}"
        )
    if eff_rr < min_rr:
        return None, f"effective R:R {eff_rr:.2f} < min {min_rr}"

    qty = position_size(float(settings["capital"]), float(settings["risk_pct_per_trade"]), entry, stop)
    reasons.append(
        f"qty = floor({settings['capital']:.0f} × {settings['risk_pct_per_trade']}% / risk {risk:.2f}) = {qty}"
    )

    return SignalLevels(
        entry=round(entry, 2),
        stop_loss=round(stop, 2),
        target_2r=round(target_2r, 2),
        target_3r=round(target_3r, 2),
        risk_reward=round(eff_rr, 2),
        pct_risk=round(pct_risk, 2),
        suggested_qty=qty,
        reasons=reasons,
    ), ""


def position_size(capital: float, risk_pct: float, entry: float, stop: float) -> int:
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0
    qty = floor((capital * risk_pct / 100.0) / risk_per_share)
    # don't let the position notional exceed capital
    if entry > 0:
        qty = min(qty, floor(capital / entry))
    return max(qty, 0)


def apply_costs(entry: float, exit_: float, qty: int, settings: dict) -> tuple[float, float]:
    """Realistic cost model for LONG paper trades and backtests.

    Slippage is applied adversely on both legs; flat brokerage per order.
    Returns (net_pnl, total_costs).
    """
    slip = float(settings["slippage_pct"]) / 100.0
    brokerage = float(settings["brokerage_per_order"])
    eff_entry = entry * (1 + slip)
    eff_exit = exit_ * (1 - slip)
    gross = (exit_ - entry) * qty
    net = (eff_exit - eff_entry) * qty - 2 * brokerage
    return round(net, 2), round(gross - net, 2)
