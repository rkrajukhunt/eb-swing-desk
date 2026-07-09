"""Backtester — date-driven portfolio simulation.

Reuses the EXACT modules the live scanner uses — compute_indicators,
liquidity_check, evaluate_strategies, build_levels, apply_costs — so validating a
strategy here validates the same code path that produces live signals.

Execution model (conservative):
  * Signal on bar i → entry at bar i+1 OPEN (no same-bar fills, no look-ahead).
  * SL / target checked against each subsequent bar's low/high; if both fall in
    one bar the STOP fills first (worst case). Time stop after
    `backtest_max_holding_days` bars → exit at close.
  * ONE shared capital pool across all symbols (Phase 1.5): positions compete for
    capital and for at most `backtest_max_positions` concurrent slots, chosen by
    composite score. Equity is marked to market DAILY — so Sharpe, CAGR and
    drawdown are real portfolio numbers, not a serialized sum of per-trade P&L.
  * A NIFTY buy-and-hold benchmark over the same window is reported alongside; if
    the strategy doesn't beat it net of costs, it has no edge.
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from ..database import db_session
from ..engine.indicators import compute_indicators
from ..engine.liquidity import liquidity_check
from ..engine.signals import apply_costs, build_levels, position_size
from ..engine.scoring import score_candidate
from ..engine.strategies import evaluate_strategies
from ..models import AppSetting
from ..services import market_data
from ..services.settings_store import get_settings
from ..services.universe import INDEX_SYMBOL, get_universe_symbols
from .metrics import compute_metrics, _sharpe, _cagr_pct

log = logging.getLogger(__name__)

_TRIALS_KEY = "meta_backtest_trials"


def _regime_series(index_ind: pd.DataFrame) -> pd.Series:
    """Per-bar 'does the index regime allow longs' — same rules as regime.py."""
    above200 = index_ind["close"] > index_ind["ema200"]
    e50_rising = index_ind["ema50"] > index_ind["ema50"].shift(11)
    bullish = above200 & ((index_ind["plus_di"] > index_ind["minus_di"]) | e50_rising)
    bearish = (~above200) & (index_ind["adx14"] > 20) & (index_ind["minus_di"] > index_ind["plus_di"])
    return ~bearish | bullish


def _bump_trials() -> int:
    """Persist how many backtests have been run against this dataset. López de
    Prado: track the trial count so the Sharpe can be honestly deflated for
    multiple testing."""
    with db_session() as s:
        row = s.get(AppSetting, _TRIALS_KEY)
        n = int((row.value or {}).get("count", 0)) + 1 if row else 1
        if row is None:
            s.add(AppSetting(key=_TRIALS_KEY, value={"count": n}))
        else:
            row.value = {"count": n}
    return n


def _benchmark(index_daily: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict | None:
    """NIFTY buy-and-hold over [start, end]."""
    if index_daily.empty:
        return None
    win = index_daily[(index_daily.index >= start) & (index_daily.index <= end)]
    if len(win) < 20:
        return None
    closes = win["close"].tolist()
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    n_days = max((win.index[-1] - win.index[0]).days, 1)
    return {
        "return_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
        "cagr_pct": _cagr_pct(closes, n_days),
        "sharpe": _sharpe(rets),
    }


def _precompute(sym: str, settings: dict, index_daily: pd.DataFrame,
                allows: pd.Series | None, only: str | None,
                cutoff: pd.Timestamp):
    """Returns (ohlc_by_ts, pending_entries) for one symbol, or None if skipped.
    `pending_entries`: list of dicts keyed later by entry timestamp."""
    daily = market_data.load_daily(sym)
    if len(daily) < 260:
        return None
    ind = compute_indicators(daily, market_data.to_weekly(daily), index_daily,
                             swing_low_lookback=int(settings["swing_low_lookback"]))
    ok, _ = liquidity_check(ind, settings)
    if not ok:
        return None

    idx = ind.index
    start = max(210, int(idx.searchsorted(cutoff)))
    ohlc = {idx[i]: (float(ind.iloc[i]["open"]), float(ind.iloc[i]["high"]),
                     float(ind.iloc[i]["low"]), float(ind.iloc[i]["close"]))
            for i in range(start - 1, len(ind))}

    entries = []
    for i in range(start, len(ind) - 1):
        row = ind.iloc[i]
        regime_ok = True
        if allows is not None:
            pos = int(allows.index.searchsorted(idx[i], side="right")) - 1
            if pos >= 0:
                regime_ok = bool(allows.iloc[pos])
        matches = evaluate_strategies(row, settings["strategies"], regime_ok, only=only)
        if not matches:
            continue
        m = matches[0]
        levels, _rej = build_levels(row, m.strategy, settings)
        if levels is None:
            continue
        # entry at NEXT bar's open; scale SL/target by the fill slippage
        next_open = float(ind.iloc[i + 1]["open"])
        ratio = next_open / levels.entry if levels.entry > 0 else 1.0
        score, _subs, _pen = score_candidate(row, levels.risk_reward)
        entries.append({
            "sym": sym, "strategy": m.strategy, "entry_ts": idx[i + 1],
            "entry": next_open, "sl": levels.stop_loss * ratio,
            "tgt": levels.target_2r * ratio, "score": score,
        })
    return ohlc, entries


def run_backtest(strategy: str = "all", years: int | None = None,
                 symbols: list[str] | None = None) -> dict:
    settings = get_settings()
    years = years or int(settings["backtest_years"])
    max_hold = int(settings["backtest_max_holding_days"])
    capital = float(settings["capital"])
    risk_pct = float(settings["risk_pct_per_trade"])
    max_positions = int(settings.get("backtest_max_positions", 10))
    universe = symbols or get_universe_symbols(settings["universe"])
    only = None if strategy in ("all", "", None) else strategy

    index_daily = market_data.load_daily(INDEX_SYMBOL)
    index_ind = compute_indicators(index_daily, market_data.to_weekly(index_daily)) \
        if not index_daily.empty else pd.DataFrame()
    allows = _regime_series(index_ind) if not index_ind.empty else None
    cutoff = pd.Timestamp(datetime.now()) - pd.DateOffset(years=years)

    # --- precompute per symbol: OHLC lookup + pending entries -----------------
    ohlc: dict[str, dict] = {}
    entries_on: dict[pd.Timestamp, list[dict]] = {}
    tested_symbols = 0
    for sym in universe:
        res = _precompute(sym, settings, index_daily, allows, only, cutoff)
        if res is None:
            continue
        tested_symbols += 1
        sym_ohlc, sym_entries = res
        ohlc[sym] = sym_ohlc
        for e in sym_entries:
            entries_on.setdefault(e["entry_ts"], []).append(e)

    # master trading calendar = index dates within the window (fallback: union)
    if not index_ind.empty:
        dates = [d for d in index_ind.index if d >= cutoff]
    else:
        dates = sorted({d for m in ohlc.values() for d in m})
        dates = [d for d in dates if d >= cutoff]

    # --- day-by-day portfolio simulation --------------------------------------
    cash = capital
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for d in dates:
        # 1. exits (stop-first), realizing P&L back into cash
        for sym in list(positions):
            bar = ohlc.get(sym, {}).get(d)
            if bar is None:
                continue
            o, hi, lo, c = bar
            p = positions[sym]
            exit_price = reason = None
            if lo <= p["sl"]:
                exit_price, reason = p["sl"], "Stopped Out"
            elif hi >= p["tgt"]:
                exit_price, reason = p["tgt"], "Target Hit"
            elif (d - p["entry_ts"]).days >= max_hold * 1.5:  # calendar ~ trading days
                exit_price, reason = c, "Time Stop"
            if exit_price is None:
                continue
            net, _costs = apply_costs(p["entry"], exit_price, p["qty"], settings)
            cash += p["qty"] * exit_price - _costs
            risk = p["entry"] - p["init_sl"]
            trades.append({
                "symbol": sym, "strategy": p["strategy"],
                "entry_time": str(p["entry_ts"].date()), "exit_time": str(d.date()),
                "entry": round(p["entry"], 2), "exit": round(exit_price, 2),
                "qty": p["qty"], "reason": reason, "pnl": net,
                "r_multiple": round((exit_price - p["entry"]) / risk, 2) if risk > 0 else None,
                "holding_days": (d - p["entry_ts"]).days,
            })
            del positions[sym]

        # 2. entries — fill this day's pending signals by score, respecting slots + cash
        pending = sorted(entries_on.get(d, []), key=lambda e: e["score"], reverse=True)
        for e in pending:
            if len(positions) >= max_positions:
                break
            if e["sym"] in positions:
                continue
            equity_now = cash + sum(positions[s]["qty"] * ohlc.get(s, {}).get(d, (0, 0, 0, positions[s]["entry"]))[3]
                                    for s in positions)
            qty = position_size(equity_now, risk_pct, e["entry"], e["sl"])
            qty = min(qty, int(cash // e["entry"]) if e["entry"] > 0 else 0)
            if qty <= 0:
                continue
            cash -= qty * e["entry"]
            positions[e["sym"]] = {
                "strategy": e["strategy"], "entry_ts": e["entry_ts"], "entry": e["entry"],
                "sl": e["sl"], "init_sl": e["sl"], "tgt": e["tgt"], "qty": qty,
            }

        # 3. mark to market → daily equity point
        mtm = sum(positions[s]["qty"] * ohlc.get(s, {}).get(d, (0, 0, 0, positions[s]["entry"]))[3]
                  for s in positions)
        equity_curve.append({"t": str(d.date()), "equity": round(cash + mtm, 2)})

    n_trials = _bump_trials()
    benchmark = _benchmark(index_daily, dates[0], dates[-1]) if dates else None
    overall = compute_metrics(trades, capital, daily_equity=equity_curve,
                              benchmark=benchmark, n_trials=n_trials)
    by_strategy = {}
    for name in {t["strategy"] for t in trades}:
        by_strategy[name] = compute_metrics([t for t in trades if t["strategy"] == name], capital)

    return {
        "strategy": strategy or "all",
        "years": years,
        "symbols_tested": tested_symbols,
        "max_positions": max_positions,
        "n_trials": n_trials,
        "overall": overall,
        "by_strategy": by_strategy,
        "trades": trades[-500:],
    }
