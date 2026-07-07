from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..adapters.factory import active_adapter
from ..config import DISCLAIMER
from ..database import db_session
from ..engine.scanner import get_current_regime, latest_scan, run_scan
from ..engine.strategies import STRATEGY_LABELS
from ..models import ScanRun, Signal

router = APIRouter(prefix="/api", tags=["scan"])

_scan_lock = asyncio.Lock()


class ScanRequest(BaseModel):
    strategy: str = "all"
    refresh: bool = True


def _signal_dict(sig: Signal) -> dict:
    return {
        "id": sig.id, "symbol": sig.symbol,
        "strategy": sig.strategy, "strategy_label": STRATEGY_LABELS.get(sig.strategy, sig.strategy),
        "composite_score": sig.composite_score, "sub_scores": sig.sub_scores,
        "penalties": sig.penalties,
        "entry": sig.entry, "stop_loss": sig.stop_loss,
        "target_2r": sig.target_2r, "target_3r": sig.target_3r,
        "risk_reward": sig.risk_reward, "pct_risk": sig.pct_risk,
        "suggested_qty": sig.suggested_qty,
        "indicators": sig.indicators, "reasons": sig.reasons,
        "llm_rank": sig.llm_rank, "llm_conviction": sig.llm_conviction,
        "llm_rationale": sig.llm_rationale, "llm_conflicts": sig.llm_conflicts,
        "llm_regime_note": sig.llm_regime_note,
    }


@router.post("/scan/run")
async def scan_run(req: ScanRequest):
    if _scan_lock.locked():
        raise HTTPException(409, "a scan is already running")
    async with _scan_lock:
        try:
            run_id = await asyncio.to_thread(run_scan, active_adapter(), req.strategy, req.refresh)
        except Exception as e:
            raise HTTPException(502, f"scan failed: {e}") from e
    return {"scan_run_id": run_id}


@router.get("/scan/latest")
def scan_latest():
    with db_session() as s:
        run = latest_scan(s)
        if run is None:
            return {"run": None, "signals": [], "disclaimer": DISCLAIMER}
        signals = list(s.execute(
            select(Signal).where(Signal.scan_run_id == run.id)
        ).scalars())
    # LLM order when available, else deterministic score order
    signals.sort(key=lambda x: (x.llm_rank if x.llm_rank is not None else 10_000,
                                -x.composite_score))
    return {
        "run": {
            "id": run.id, "started_at": run.started_at.isoformat(),
            "universe": run.universe, "strategy_filter": run.strategy_filter,
            "market_regime": run.market_regime, "regime_detail": run.regime_detail,
            "scanned_count": run.scanned_count, "signal_count": run.signal_count,
            "llm_used": run.llm_used,
        },
        "signals": [_signal_dict(x) for x in signals],
        "disclaimer": DISCLAIMER,
    }


@router.get("/regime")
def regime():
    try:
        r = get_current_regime(active_adapter(), refresh=False)
        return {"regime": r.label, "detail": r.detail, "allows_longs": r.allows_longs}
    except Exception as e:
        return {"regime": "unknown", "detail": {"error": str(e)}, "allows_longs": False}
