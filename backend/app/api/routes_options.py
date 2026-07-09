from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..adapters.base import BrokerError
from ..adapters.factory import active_adapter
from ..config import DISCLAIMER
from ..database import db_session
from ..models import OptionSignal, OptionTrade
from ..options import paper as opt_paper
from ..options.engine import generate_signal
from ..options.structures import STRATEGY_LABELS

router = APIRouter(prefix="/api/options", tags=["options"])

_gen_lock = asyncio.Lock()


class GenerateRequest(BaseModel):
    strategy: str = "auto"   # auto | bull_put_spread | bear_call_spread | iron_condor


class OpenOptionTradeRequest(BaseModel):
    signal_id: int
    lots: int


def _signal_dict(sig: OptionSignal) -> dict:
    return {
        "id": sig.id, "created_at": sig.created_at.isoformat(),
        "underlying": sig.underlying, "expiry": sig.expiry, "dte": sig.dte,
        "spot": sig.spot, "regime": sig.regime,
        "strategy": sig.strategy,
        "strategy_label": STRATEGY_LABELS.get(sig.strategy, sig.strategy),
        "entry_ok": sig.entry_ok, "entry_block_reason": sig.entry_block_reason,
        "atm_iv_pct": sig.atm_iv_pct, "expected_move": sig.expected_move,
        "legs": sig.legs, "credit": sig.credit, "max_profit": sig.max_profit,
        "max_loss": sig.max_loss, "margin_per_lot": sig.margin_per_lot,
        "roc_pct": sig.roc_pct, "pop_pct": sig.pop_pct,
        "breakevens": sig.breakevens, "lots_suggested": sig.lots_suggested,
        "target_met": sig.target_met, "reasons": sig.reasons,
        "chain_snapshot": sig.chain_snapshot,
    }


def _trade_dict(t: OptionTrade) -> dict:
    return {
        "id": t.id, "signal_id": t.signal_id, "strategy": t.strategy,
        "strategy_label": STRATEGY_LABELS.get(t.strategy, t.strategy),
        "expiry": t.expiry, "lots": t.lots, "lot_size": t.lot_size, "legs": t.legs,
        "net_credit": t.net_credit, "max_loss": t.max_loss, "margin_est": t.margin_est,
        "breakevens": t.breakevens, "short_strikes": t.short_strikes,
        "status": t.status, "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        "exit_debit": t.exit_debit, "exit_reason": t.exit_reason,
        "spot_entry": t.spot_entry, "spot_exit": t.spot_exit,
        "costs": t.costs, "pnl": t.pnl, "return_on_margin_pct": t.return_on_margin_pct,
        "broker_source": t.broker_source,
    }


@router.post("/signal/generate")
async def signal_generate(req: GenerateRequest):
    if _gen_lock.locked():
        raise HTTPException(409, "signal generation already running")
    force = None if req.strategy == "auto" else req.strategy
    async with _gen_lock:
        try:
            sig_id = await asyncio.to_thread(generate_signal, active_adapter(), force)
        except Exception as e:
            raise HTTPException(502, f"signal generation failed: {e}") from e
    with db_session() as s:
        sig = s.get(OptionSignal, sig_id)
        if sig is None:                      # generator returned an id we can't reload
            raise HTTPException(500, "signal was generated but could not be loaded")
        return {"signal": _signal_dict(sig), "disclaimer": DISCLAIMER}


@router.get("/signal/latest")
def signal_latest():
    with db_session() as s:
        sig = s.execute(
            select(OptionSignal).order_by(OptionSignal.id.desc()).limit(1)
        ).scalar_one_or_none()
        return {"signal": _signal_dict(sig) if sig else None, "disclaimer": DISCLAIMER}


@router.post("/paper/trades")
def open_trade(req: OpenOptionTradeRequest):
    try:
        return _trade_dict(opt_paper.open_trade(req.signal_id, req.lots))
    except (ValueError, BrokerError) as e:
        raise HTTPException(400, str(e)) from e


@router.get("/paper/positions")
def positions():
    return {"positions": opt_paper.positions_snapshot()}


@router.post("/paper/trades/{trade_id}/exit")
def exit_trade(trade_id: int):
    try:
        return _trade_dict(opt_paper.manual_exit(trade_id))
    except (ValueError, BrokerError) as e:
        raise HTTPException(400, str(e)) from e


@router.get("/paper/trades")
def list_trades(status: str = "closed", limit: int = 200):
    with db_session() as s:
        q = select(OptionTrade).order_by(OptionTrade.id.desc()).limit(limit)
        if status in ("open", "closed"):
            q = q.where(OptionTrade.status == status)
        trades = list(s.execute(q).scalars())
    return {"trades": [_trade_dict(t) for t in trades]}


@router.get("/paper/performance")
def performance():
    return opt_paper.weekly_performance()
