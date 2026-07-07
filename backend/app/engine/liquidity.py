"""Liquidity & quality guards — reject junk before any scoring happens."""
from __future__ import annotations

import pandas as pd


def liquidity_check(ind: pd.DataFrame, settings: dict) -> tuple[bool, str]:
    """Returns (passes, reason). `ind` is the computed indicator frame."""
    if len(ind) < int(settings["min_history_candles"]):
        return False, f"history {len(ind)} < {settings['min_history_candles']} candles (recently listed)"

    row = ind.iloc[-1]
    price = float(row["close"])
    if price < float(settings["min_price"]):
        return False, f"price ₹{price:.2f} < min ₹{settings['min_price']}"

    turnover_cr = float(row["turnover_avg20"]) / 1e7  # ₹ → crore
    if pd.isna(turnover_cr) or turnover_cr < float(settings["min_avg_turnover_cr"]):
        return False, f"avg turnover ₹{turnover_cr:.1f}cr < ₹{settings['min_avg_turnover_cr']}cr"

    # Circuit-locked / stale detection: many identical closes with near-zero range
    tail = ind.tail(5)
    ranges = (tail["high"] - tail["low"]) / tail["close"]
    if (ranges < 0.0005).sum() >= 3:
        return False, "possible circuit lock / stale prices (no intraday range)"

    if float(row["volume"]) <= 0:
        return False, "zero volume on last candle"

    return True, "ok"
