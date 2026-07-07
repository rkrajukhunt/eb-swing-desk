from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..adapters.base import BrokerError
from ..backtest.metrics import compute_metrics
from ..database import db_session
from ..models import PaperTrade, Signal
from ..paper import engine as paper
from ..services.settings_store import get_settings

router = APIRouter(prefix="/api/paper", tags=["paper"])


class OpenTradeRequest(BaseModel):
    symbol: str
    qty: int
    stop_loss: float
    target: float
    strategy: str = "manual"
    signal_id: int | None = None
    trailing: bool = False


class UpdateTradeRequest(BaseModel):
    stop_loss: float | None = None
    target: float | None = None
    trailing: bool | None = None


def _trade_dict(t: PaperTrade) -> dict:
    return {
        "id": t.id, "symbol": t.symbol, "strategy": t.strategy, "status": t.status,
        "qty": t.qty, "entry_price": t.entry_price, "stop_loss": t.stop_loss,
        "target": t.target, "trailing": t.trailing,
        "entry_time": t.entry_time.isoformat(),
        "exit_time": t.exit_time.isoformat() if t.exit_time else None,
        "exit_price": t.exit_price, "exit_reason": t.exit_reason,
        "pnl": t.pnl, "costs": t.costs, "r_multiple": t.r_multiple,
        "holding_days": t.holding_days, "broker_source": t.broker_source,
        "signal_id": t.signal_id,
    }


@router.post("/trades")
def open_trade(req: OpenTradeRequest):
    trail_atr = 0.0
    if req.trailing and req.signal_id:
        with db_session() as s:
            sig = s.get(Signal, req.signal_id)
            if sig and sig.indicators.get("atr14"):
                trail_atr = float(sig.indicators["atr14"])
    try:
        trade = paper.open_trade(
            symbol=req.symbol.upper(), qty=req.qty, stop_loss=req.stop_loss,
            target=req.target, strategy=req.strategy, signal_id=req.signal_id,
            trailing=req.trailing, trail_atr=trail_atr,
        )
    except (ValueError, BrokerError) as e:
        raise HTTPException(400, str(e)) from e
    return _trade_dict(trade)


@router.get("/positions")
def positions():
    return {"positions": paper.positions_snapshot()}


@router.post("/trades/{trade_id}/exit")
def exit_trade(trade_id: int):
    try:
        return _trade_dict(paper.manual_exit(trade_id))
    except (ValueError, BrokerError) as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/trades/{trade_id}")
def patch_trade(trade_id: int, req: UpdateTradeRequest):
    try:
        return _trade_dict(paper.update_trade(trade_id, req.stop_loss, req.target, req.trailing))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/trades")
def list_trades(status: str = "closed", limit: int = 200):
    with db_session() as s:
        q = select(PaperTrade).order_by(PaperTrade.id.desc()).limit(limit)
        if status in ("open", "closed"):
            q = q.where(PaperTrade.status == status)
        trades = list(s.execute(q).scalars())
    return {"trades": [_trade_dict(t) for t in trades]}


@router.get("/performance")
def performance():
    """Live paper-trade analytics, sliced by strategy — same metric formulas
    as the backtester (backtest/metrics.py)."""
    settings = get_settings()
    capital = float(settings["capital"])
    with db_session() as s:
        closed = list(s.execute(
            select(PaperTrade).where(PaperTrade.status == "closed").order_by(PaperTrade.exit_time)
        ).scalars())
    dicts = [_trade_dict(t) for t in closed]
    by_strategy = {}
    for name in sorted({t["strategy"] for t in dicts}):
        by_strategy[name] = compute_metrics([t for t in dicts if t["strategy"] == name], capital)
    return {
        "overall": compute_metrics(dicts, capital),
        "by_strategy": by_strategy,
    }
