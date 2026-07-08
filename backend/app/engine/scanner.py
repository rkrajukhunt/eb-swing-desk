"""Scan orchestrator: regime → liquidity guards → strategies → signal math →
scoring → persistence → (optional) LLM re-ranking.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from ..adapters.base import BrokerAdapter
from ..database import db_session
from ..models import ScanRun, Signal
from ..services import market_data
from ..services.settings_store import get_settings
from ..services.universe import INDEX_SYMBOL, get_universe_symbols
from .indicators import compute_indicators
from .liquidity import liquidity_check
from .regime import Regime, classify_regime
from .scoring import score_candidate
from .signals import build_levels
from .strategies import evaluate_strategies

log = logging.getLogger(__name__)


def _indicator_snapshot(row) -> dict:
    def f(key, nd=2):
        import pandas as pd

        v = row[key]
        return round(float(v), nd) if pd.notna(v) else None

    return {
        "ema20": f("ema20"), "ema50": f("ema50"), "ema200": f("ema200"),
        "rsi14": f("rsi14", 1), "rsi_divergence": str(row["rsi_divergence"]),
        "macd_hist": f("macd_hist"), "adx": f("adx14", 1),
        "plus_di": f("plus_di", 1), "minus_di": f("minus_di", 1),
        "atr14": f("atr14"), "bb_upper": f("bb_upper"), "bb_lower": f("bb_lower"),
        "vol_vs_avg": f("vol_ratio"), "rel_strength_55d": f("rel_strength_55d", 1),
        "dist_52w_high_pct": f("dist_52w_high_pct", 1),
        "weekly_trend": "up" if bool(row["weekly_up"]) else "down",
        "turnover_cr_20d": round(float(row["turnover_avg20"]) / 1e7, 1),
    }


def get_current_regime(adapter: BrokerAdapter, refresh: bool = True) -> Regime:
    if refresh:
        try:
            market_data.refresh_symbol(adapter, INDEX_SYMBOL)
        except Exception as e:
            log.warning("index refresh failed: %s", e)
    return classify_regime(market_data.load_daily(INDEX_SYMBOL))


def run_scan(adapter: BrokerAdapter, strategy_filter: str = "all", refresh: bool = True) -> int:
    """Runs a full scan; returns the ScanRun id."""
    settings = get_settings()
    symbols = get_universe_symbols(settings["universe"])

    if refresh:
        market_data.refresh_universe(adapter, symbols)
    regime = get_current_regime(adapter, refresh=refresh)
    index_daily = market_data.load_daily(INDEX_SYMBOL)

    only = None if strategy_filter in ("all", "", None) else strategy_filter
    candidates = []
    scanned = 0

    for sym in symbols:
        daily = market_data.load_daily(sym)
        if daily.empty:
            continue
        scanned += 1
        weekly = market_data.to_weekly(daily)
        ind = compute_indicators(daily, weekly, index_daily,
                                 swing_low_lookback=int(settings["swing_low_lookback"]))
        ok, reason = liquidity_check(ind, settings)
        if not ok:
            log.debug("%s rejected: %s", sym, reason)
            continue
        row = ind.iloc[-1]
        matches = evaluate_strategies(row, settings["strategies"], regime.allows_longs, only=only)
        for m in matches:
            levels, reject = build_levels(row, m.strategy, settings)
            if levels is None:
                log.debug("%s %s rejected by signal math: %s", sym, m.strategy, reject)
                continue
            score, subs, penalties = score_candidate(row, levels.risk_reward)
            candidates.append({
                "symbol": sym,
                "strategy": m.strategy,
                "score": score,
                "subs": subs,
                "penalties": penalties,
                "levels": levels,
                "reasons": m.reasons + levels.reasons,
                "indicators": _indicator_snapshot(row),
            })

    # Keep the best strategy match per symbol, then rank by score
    best: dict[str, dict] = {}
    for c in candidates:
        if c["symbol"] not in best or c["score"] > best[c["symbol"]]["score"]:
            best[c["symbol"]] = c
    ranked = sorted(best.values(), key=lambda c: c["score"], reverse=True)

    with db_session() as s:
        run = ScanRun(
            started_at=datetime.utcnow(),
            universe=settings["universe"],
            strategy_filter=strategy_filter or "all",
            market_regime=regime.label,
            regime_detail=regime.detail,
            scanned_count=scanned,
            signal_count=len(ranked),
            settings_snapshot={k: settings[k] for k in
                               ("atr_stop_mult", "swing_low_lookback", "min_rr",
                                "min_avg_turnover_cr", "min_price", "strategies")},
        )
        s.add(run)
        s.flush()
        for c in ranked:
            lv = c["levels"]
            s.add(Signal(
                scan_run_id=run.id, symbol=c["symbol"], strategy=c["strategy"],
                composite_score=c["score"], sub_scores=c["subs"], penalties=c["penalties"],
                entry=lv.entry, stop_loss=lv.stop_loss, target_2r=lv.target_2r,
                target_3r=lv.target_3r, risk_reward=lv.risk_reward, pct_risk=lv.pct_risk,
                suggested_qty=lv.suggested_qty, indicators=c["indicators"],
                reasons=c["reasons"],
            ))
        run_id = run.id

    # LLM layer — additive only; failures fall back to deterministic order
    if settings.get("llm_enabled") and ranked:
        try:
            from ..llm.ranker import rank_with_claude

            rank_with_claude(run_id, regime)
        except Exception:
            log.exception("LLM ranking failed — deterministic order retained")
            with db_session() as s:
                run = s.get(ScanRun, run_id)
                run.llm_used = False

    return run_id


def latest_scan(session) -> ScanRun | None:
    return session.execute(
        select(ScanRun).order_by(ScanRun.id.desc()).limit(1)
    ).scalar_one_or_none()
