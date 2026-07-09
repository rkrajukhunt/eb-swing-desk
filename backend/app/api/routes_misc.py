from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..adapters.base import BrokerError
from ..adapters.factory import active_adapter, get_adapter
from ..backtest.engine import run_backtest
from ..config import DISCLAIMER, LLM_PROVIDERS
from ..database import db_session
from ..engine.indicators import ema
from ..models import Signal, WatchlistItem
from ..services import market_data
from ..services.settings_store import get_settings, update_settings
from ..services.universe import get_universe_symbols

router = APIRouter(prefix="/api", tags=["misc"])


# --- Settings ---------------------------------------------------------------
@router.get("/settings")
def read_settings():
    return get_settings()


@router.put("/settings")
def write_settings(patch: dict):
    return update_settings(patch)


@router.get("/meta")
def meta():
    s = get_settings()
    return {
        "universes": ["NIFTY50", "NIFTY100", "NIFTY500"],
        "strategies": list(s["strategies"].keys()),
        "brokers": ["yahoo", "angel_one", "zerodha"],
        # No secrets here — just ids, labels and model presets for the Settings UI.
        "llm_providers": [
            {"id": pid, "label": spec["label"], "models": spec["models"],
             "sdk": spec["sdk"], "key_prefix": spec["key_prefix"],
             "key_env": spec["key_env"].upper()}
            for pid, spec in LLM_PROVIDERS.items()
        ],
        "disclaimer": DISCLAIMER,
    }


# --- LLM ----------------------------------------------------------------------
@router.post("/llm/test")
def llm_test():
    """Round-trip the configured provider/model. The scan path degrades silently
    to deterministic ordering on any LLM failure, so this is the only way to see
    whether the key, endpoint and model id actually work."""
    from ..llm.ranker import test_connection

    return test_connection()


# --- Broker -----------------------------------------------------------------
class AuthRequest(BaseModel):
    broker: str | None = None
    request_token: str | None = None   # Zerodha flow


@router.get("/broker/status")
def broker_status():
    adapter = active_adapter()
    try:
        st = adapter.status()
        return {"name": st.name, "authenticated": st.authenticated,
                "detail": st.detail, "login_url": st.login_url}
    except Exception as e:
        return {"name": adapter.name, "authenticated": False, "detail": str(e), "login_url": None}


@router.post("/broker/authenticate")
def broker_auth(req: AuthRequest):
    adapter = get_adapter(req.broker) if req.broker else active_adapter()
    try:
        st = adapter.authenticate(request_token=req.request_token) \
            if req.request_token else adapter.authenticate()
        return {"name": st.name, "authenticated": st.authenticated, "detail": st.detail}
    except BrokerError as e:
        raise HTTPException(502, str(e)) from e


# --- Chart data ---------------------------------------------------------------
@router.get("/symbols/{symbol}/chart")
def chart(symbol: str, days: int = 260):
    symbol = symbol.upper()
    daily = market_data.load_daily(symbol)
    if daily.empty:
        raise HTTPException(404, f"no cached data for {symbol} — run a scan first")
    df = daily.tail(days).copy()
    e20 = ema(daily["close"], 20).tail(days)
    e50 = ema(daily["close"], 50).tail(days)
    e200 = ema(daily["close"], 200).tail(days)
    candles = [
        {"time": str(ts.date()), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
         "volume": r.volume}
        for ts, r in df.iterrows()
    ]
    lines = {
        "ema20": [{"time": str(ts.date()), "value": round(float(v), 2)} for ts, v in e20.items()],
        "ema50": [{"time": str(ts.date()), "value": round(float(v), 2)} for ts, v in e50.items()],
        "ema200": [{"time": str(ts.date()), "value": round(float(v), 2)} for ts, v in e200.items()],
    }
    # latest signal levels for this symbol, if any
    with db_session() as s:
        sig = s.execute(
            select(Signal).where(Signal.symbol == symbol).order_by(Signal.id.desc()).limit(1)
        ).scalar_one_or_none()
    levels = None
    if sig:
        levels = {"entry": sig.entry, "stop_loss": sig.stop_loss,
                  "target_2r": sig.target_2r, "target_3r": sig.target_3r}
    return {"symbol": symbol, "candles": candles, "emas": lines, "levels": levels}


# --- Watchlist ----------------------------------------------------------------
class WatchRequest(BaseModel):
    symbol: str
    note: str = ""


@router.get("/watchlist")
def watchlist():
    with db_session() as s:
        items = list(s.execute(select(WatchlistItem).order_by(WatchlistItem.id.desc())).scalars())
    return {"items": [{"id": i.id, "symbol": i.symbol, "note": i.note,
                       "added_at": i.added_at.isoformat()} for i in items]}


@router.post("/watchlist")
def watch_add(req: WatchRequest):
    sym = req.symbol.upper().strip()
    if not sym:
        raise HTTPException(400, "symbol required")
    with db_session() as s:
        exists = s.execute(select(WatchlistItem).where(WatchlistItem.symbol == sym)).scalar_one_or_none()
        if exists:
            raise HTTPException(409, f"{sym} already in watchlist")
        s.add(WatchlistItem(symbol=sym, note=req.note))
    return {"ok": True}


@router.delete("/watchlist/{item_id}")
def watch_remove(item_id: int):
    with db_session() as s:
        item = s.get(WatchlistItem, item_id)
        if item:
            s.delete(item)
    return {"ok": True}


# --- Backtest -------------------------------------------------------------------
class BacktestRequest(BaseModel):
    strategy: str = "all"
    years: int | None = None


_bt_lock = asyncio.Lock()


@router.post("/backtest/run")
async def backtest(req: BacktestRequest):
    if _bt_lock.locked():
        raise HTTPException(409, "a backtest is already running")
    async with _bt_lock:
        try:
            result = await asyncio.to_thread(run_backtest, req.strategy, req.years)
        except Exception as e:
            raise HTTPException(500, f"backtest failed: {e}") from e
    return result


@router.get("/universe")
def universe():
    s = get_settings()
    return {"universe": s["universe"], "symbols": get_universe_symbols(s["universe"])}


@router.get("/health")
def health():
    from sqlalchemy import text
    db_status = "ok"
    db_error = None
    try:
        with db_session() as s:
            s.execute(text("SELECT 1"))
    except Exception as e:
        db_status = "error"
        db_error = str(e)

    if db_status != "ok":
        raise HTTPException(
            status_code=500,
            detail={
                "status": "unhealthy",
                "ok": False,
                "database": {"status": "error", "error": db_error}
            }
        )

    return {
        "status": "ok",
        "ok": True,
        "database": {"status": "ok"}
    }
