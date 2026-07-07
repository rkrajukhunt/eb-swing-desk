"""Paper trading engine: open positions, live P&L, auto-exit, trailing stops.

The auto-exit loop runs as a background asyncio task (started in main.py's
lifespan). It batches LTP quotes to respect broker rate limits and only polls
while the market is open (mock broker: always, so the system is testable on
weekends).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from ..adapters.base import BrokerError
from ..adapters.factory import active_adapter
from ..database import db_session
from ..engine.signals import apply_costs
from ..models import PaperTrade
from ..services.settings_store import get_settings
from ..utils.market_hours import is_market_open

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
def open_trade(
    symbol: str,
    qty: int,
    stop_loss: float,
    target: float,
    strategy: str = "manual",
    signal_id: int | None = None,
    trailing: bool = False,
    trail_atr: float = 0.0,
) -> PaperTrade:
    """Entry price is the live LTP at click time — never a stale scan price."""
    adapter = active_adapter()
    ltp = adapter.get_ltp([symbol]).get(symbol)
    if ltp is None:
        raise BrokerError(f"No live quote for {symbol}")
    if qty <= 0:
        raise ValueError("qty must be positive")
    if stop_loss >= ltp:
        raise ValueError(f"stop loss {stop_loss} must be below entry LTP {ltp}")
    if target <= ltp:
        raise ValueError(f"target {target} must be above entry LTP {ltp}")

    with db_session() as s:
        trade = PaperTrade(
            symbol=symbol, strategy=strategy, signal_id=signal_id,
            broker_source=adapter.name, qty=qty, entry_price=ltp,
            stop_loss=stop_loss, target=target, initial_risk=ltp - stop_loss,
            trailing=trailing, trail_atr=trail_atr, highest_close=ltp,
            entry_time=datetime.utcnow(),
        )
        s.add(trade)
        s.flush()
        s.refresh(trade)
    log.info("paper BUY %s x%d @ %.2f (SL %.2f, T %.2f)", symbol, qty, ltp, stop_loss, target)
    return trade


def _close(session, trade: PaperTrade, exit_price: float, reason: str, settings: dict) -> None:
    trade.status = "closed"
    trade.exit_price = round(exit_price, 2)
    trade.exit_reason = reason
    trade.exit_time = datetime.utcnow()
    trade.holding_days = max((trade.exit_time - trade.entry_time).days, 0)
    net, costs = apply_costs(trade.entry_price, exit_price, trade.qty, settings)
    trade.pnl = net
    trade.costs = costs
    trade.r_multiple = round(
        (exit_price - trade.entry_price) / trade.initial_risk, 2
    ) if trade.initial_risk > 0 else None
    log.info("paper CLOSE %s @ %.2f (%s) pnl %.2f", trade.symbol, exit_price, reason, net)


def manual_exit(trade_id: int) -> PaperTrade:
    settings = get_settings()
    adapter = active_adapter()
    with db_session() as s:
        trade = s.get(PaperTrade, trade_id)
        if trade is None or trade.status != "open":
            raise ValueError("trade not found or already closed")
        ltp = adapter.get_ltp([trade.symbol]).get(trade.symbol)
        if ltp is None:
            raise BrokerError(f"No live quote for {trade.symbol}")
        _close(s, trade, ltp, "Manual Exit", settings)
        s.flush()
        s.refresh(trade)
    return trade


def update_trade(trade_id: int, stop_loss: float | None = None,
                 target: float | None = None, trailing: bool | None = None) -> PaperTrade:
    with db_session() as s:
        trade = s.get(PaperTrade, trade_id)
        if trade is None or trade.status != "open":
            raise ValueError("trade not found or already closed")
        if stop_loss is not None:
            trade.stop_loss = stop_loss
        if target is not None:
            trade.target = target
        if trailing is not None:
            trade.trailing = trailing
        s.flush()
        s.refresh(trade)
    return trade


# ---------------------------------------------------------------------------
def check_exits_once() -> dict:
    """One pass of the auto-exit engine. Batched quotes; SL/target/trailing."""
    settings = get_settings()
    adapter = active_adapter()
    with db_session() as s:
        open_trades = list(s.execute(
            select(PaperTrade).where(PaperTrade.status == "open")
        ).scalars())
        if not open_trades:
            return {"checked": 0, "closed": 0}

        symbols = sorted({t.symbol for t in open_trades})
        try:
            ltps = adapter.get_ltp(symbols)
        except BrokerError as e:
            log.warning("auto-exit: quote batch failed: %s", e)
            return {"checked": 0, "closed": 0, "error": str(e)}

        closed = 0
        for t in open_trades:
            ltp = ltps.get(t.symbol)
            if ltp is None:
                continue
            # Trailing stop: ratchet SL up as price advances (ATR-based)
            if t.trailing and t.trail_atr > 0:
                if ltp > t.highest_close:
                    t.highest_close = ltp
                trail_mult = float(settings["trailing_atr_mult"])
                new_sl = round(t.highest_close - t.trail_atr * trail_mult, 2)
                if new_sl > t.stop_loss:
                    t.stop_loss = new_sl
            if ltp <= t.stop_loss:
                reason = "Trail Stop" if (t.trailing and t.stop_loss > t.entry_price - t.initial_risk + 1e-9) \
                    else "Stopped Out"
                _close(s, t, ltp, reason, settings)
                closed += 1
            elif ltp >= t.target:
                _close(s, t, ltp, "Target Hit", settings)
                closed += 1
        return {"checked": len(open_trades), "closed": closed}


def positions_snapshot() -> list[dict]:
    """Open positions with live unrealized P&L for the UI."""
    settings = get_settings()
    adapter = active_adapter()
    with db_session() as s:
        open_trades = list(s.execute(
            select(PaperTrade).where(PaperTrade.status == "open").order_by(PaperTrade.id.desc())
        ).scalars())
    if not open_trades:
        return []
    try:
        ltps = adapter.get_ltp(sorted({t.symbol for t in open_trades}))
    except BrokerError:
        ltps = {}
    out = []
    for t in open_trades:
        ltp = ltps.get(t.symbol)
        upnl = upnl_pct = r_mult = dist_sl = dist_tgt = None
        if ltp is not None:
            net, _ = apply_costs(t.entry_price, ltp, t.qty, settings)
            upnl = net
            upnl_pct = round((ltp / t.entry_price - 1) * 100, 2)
            if t.initial_risk > 0:
                r_mult = round((ltp - t.entry_price) / t.initial_risk, 2)
            dist_sl = round((ltp - t.stop_loss) / ltp * 100, 2)
            dist_tgt = round((t.target - ltp) / ltp * 100, 2)
        out.append({
            "id": t.id, "symbol": t.symbol, "strategy": t.strategy, "qty": t.qty,
            "entry_price": t.entry_price, "stop_loss": t.stop_loss, "target": t.target,
            "trailing": t.trailing, "entry_time": t.entry_time.isoformat(),
            "ltp": ltp, "unrealized_pnl": upnl, "unrealized_pnl_pct": upnl_pct,
            "r_multiple": r_mult, "dist_to_sl_pct": dist_sl, "dist_to_target_pct": dist_tgt,
            "broker_source": t.broker_source,
        })
    return out


# ---------------------------------------------------------------------------
_stop_event: asyncio.Event | None = None


async def auto_exit_loop() -> None:
    """Background task started from the FastAPI lifespan."""
    global _stop_event
    _stop_event = asyncio.Event()
    log.info("auto-exit engine started")
    while not _stop_event.is_set():
        settings = get_settings()
        try:
            # Mock broker trades any time (dev); real brokers only in market hours
            if settings["active_broker"] == "mock" or is_market_open():
                await asyncio.to_thread(check_exits_once)
        except Exception:
            log.exception("auto-exit pass failed")
        try:
            await asyncio.wait_for(_stop_event.wait(),
                                   timeout=max(int(settings["auto_exit_poll_seconds"]), 5))
        except asyncio.TimeoutError:
            pass


def stop_auto_exit_loop() -> None:
    if _stop_event is not None:
        _stop_event.set()
