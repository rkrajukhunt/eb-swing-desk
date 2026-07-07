"""Backtester.

Deliberately reuses the EXACT same modules the live scanner uses —
compute_indicators, liquidity_check, evaluate_strategies, build_levels,
apply_costs — iterated bar by bar. Validating a strategy here validates the
same code path that produces live signals.

Execution model (conservative):
  * Signal on bar i → entry at bar i+1 OPEN (no same-bar fills).
  * SL / target checked against each subsequent bar's low/high; if both are
    inside one bar, the STOP is assumed to fill first (worst case).
  * Time stop after `backtest_max_holding_days` bars → exit at close.
  * One open position per symbol; costs identical to paper trading.
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from ..engine.indicators import compute_indicators
from ..engine.liquidity import liquidity_check
from ..engine.regime import classify_regime
from ..engine.signals import apply_costs, build_levels, position_size
from ..engine.strategies import evaluate_strategies
from ..services import market_data
from ..services.settings_store import get_settings
from ..services.universe import INDEX_SYMBOL, get_universe_symbols
from .metrics import compute_metrics

log = logging.getLogger(__name__)


def _regime_series(index_ind: pd.DataFrame) -> pd.Series:
    """Per-bar 'does the index regime allow longs' — same rules as regime.py,
    vectorized so the backtest can look it up per day."""
    above200 = index_ind["close"] > index_ind["ema200"]
    e50_rising = index_ind["ema50"] > index_ind["ema50"].shift(11)
    bullish = above200 & ((index_ind["plus_di"] > index_ind["minus_di"]) | e50_rising)
    bearish = (~above200) & (index_ind["adx14"] > 20) & (index_ind["minus_di"] > index_ind["plus_di"])
    return ~bearish | bullish  # allows longs = not confirmed-bearish


def run_backtest(strategy: str = "all", years: int | None = None,
                 symbols: list[str] | None = None) -> dict:
    settings = get_settings()
    years = years or int(settings["backtest_years"])
    max_hold = int(settings["backtest_max_holding_days"])
    capital = float(settings["capital"])
    universe = symbols or get_universe_symbols(settings["universe"])
    only = None if strategy in ("all", "", None) else strategy

    index_daily = market_data.load_daily(INDEX_SYMBOL)
    index_ind = compute_indicators(index_daily, market_data.to_weekly(index_daily)) \
        if not index_daily.empty else pd.DataFrame()
    allows = _regime_series(index_ind) if not index_ind.empty else None

    cutoff = pd.Timestamp(datetime.now()) - pd.DateOffset(years=years)
    trades: list[dict] = []
    tested_symbols = 0

    for sym in universe:
        daily = market_data.load_daily(sym)
        if len(daily) < 260:
            continue
        tested_symbols += 1
        ind = compute_indicators(daily, market_data.to_weekly(daily), index_daily,
                                 swing_low_lookback=int(settings["swing_low_lookback"]))
        ok, _ = liquidity_check(ind, settings)
        if not ok:
            continue

        idx = ind.index
        start = max(210, idx.searchsorted(cutoff))
        open_pos: dict | None = None

        for i in range(start, len(ind) - 1):
            row = ind.iloc[i]
            ts = idx[i]

            if open_pos is not None:
                bar = ind.iloc[i]
                exit_price = None
                reason = None
                if float(bar["low"]) <= open_pos["stop_loss"]:
                    exit_price, reason = open_pos["stop_loss"], "Stopped Out"
                elif float(bar["high"]) >= open_pos["target"]:
                    exit_price, reason = open_pos["target"], "Target Hit"
                elif i - open_pos["entry_i"] >= max_hold:
                    exit_price, reason = float(bar["close"]), "Time Stop"
                if exit_price is not None:
                    net, _costs = apply_costs(open_pos["entry"], exit_price, open_pos["qty"], settings)
                    risk = open_pos["entry"] - open_pos["init_sl"]
                    trades.append({
                        "symbol": sym, "strategy": open_pos["strategy"],
                        "entry_time": str(idx[open_pos["entry_i"]].date()),
                        "exit_time": str(ts.date()),
                        "entry": round(open_pos["entry"], 2),
                        "exit": round(exit_price, 2), "qty": open_pos["qty"],
                        "reason": reason, "pnl": net,
                        "r_multiple": round((exit_price - open_pos["entry"]) / risk, 2) if risk > 0 else None,
                        "holding_days": (ts - idx[open_pos["entry_i"]]).days,
                    })
                    open_pos = None
                else:
                    continue  # still holding

            if open_pos is not None:
                continue

            # regime gate per bar (same rule as live scanner)
            regime_ok = True
            if allows is not None:
                pos = allows.index.searchsorted(ts, side="right") - 1
                if pos >= 0:
                    regime_ok = bool(allows.iloc[pos])
            matches = evaluate_strategies(row, settings["strategies"], regime_ok, only=only)
            if not matches:
                continue
            m = matches[0]
            levels, _rej = build_levels(row, m.strategy, settings)
            if levels is None:
                continue
            # enter at next bar's open; keep SL/target distances from signal bar
            next_open = float(ind.iloc[i + 1]["open"])
            slip_ratio = next_open / levels.entry if levels.entry > 0 else 1.0
            sl = levels.stop_loss * slip_ratio
            tgt = levels.target_2r * slip_ratio
            qty = position_size(capital, float(settings["risk_pct_per_trade"]), next_open, sl)
            if qty <= 0:
                continue
            open_pos = {
                "strategy": m.strategy, "entry_i": i + 1, "entry": next_open,
                "stop_loss": sl, "init_sl": sl, "target": tgt, "qty": qty,
            }

    overall = compute_metrics(trades, capital)
    by_strategy = {}
    for name in {t["strategy"] for t in trades}:
        by_strategy[name] = compute_metrics([t for t in trades if t["strategy"] == name], capital)

    return {
        "strategy": strategy or "all",
        "years": years,
        "symbols_tested": tested_symbols,
        "overall": overall,
        "by_strategy": by_strategy,
        "trades": trades[-500:],  # cap payload
    }
