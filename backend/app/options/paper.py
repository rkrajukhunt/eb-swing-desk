"""Options paper-trading engine (multi-leg, short premium, LIVE quotes).

Entry fills every leg at its live quote at click time (with adverse slippage),
MTM polls live leg quotes, and the auto-exit pass enforces:
    Profit Target      — pnl ≥ profit_take_pct_of_max × max profit
    Stop Loss          — pnl ≤ −stop_loss_mult_of_credit × credit received
    Strike Breach      — spot beyond a short strike (optional)
    Expiry Settlement  — at/after expiry cutoff, settle legs at intrinsic
Weekly performance buckets closed trades by ISO week and reports return on
margin against the configured weekly target.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import select

from ..adapters.base import BrokerError
from ..adapters.factory import active_adapter
from ..database import db_session
from ..models import OptionSignal, OptionTrade
from ..services.settings_store import get_settings
from ..utils.market_hours import IST
from .expiries import EXPIRY_CUTOFF

log = logging.getLogger(__name__)


def _leg_sign(side: str) -> int:
    return -1 if side == "sell" else 1  # cash flow sign per unit premium


def _net_debit(legs: list[dict], quotes: dict[str, float]) -> float | None:
    """Cost (points/share) to CLOSE the position at current quotes.
    Positive = you pay to close. None if any leg quote is missing."""
    total = 0.0
    for leg in legs:
        q = quotes.get(f"{leg['strike']}{leg['opt_type']}")
        if q is None:
            return None
        # closing reverses each leg: buy back sells, sell out buys
        total += q if leg["side"] == "sell" else -q
    return total


def _entry_costs(legs: list[dict], lots: int, lot_size: int, opt: dict) -> float:
    slip = float(opt["slippage_pct_premium"]) / 100.0
    brokerage = float(opt["brokerage_per_leg"])
    prem_slip = sum(l["entry_price"] * slip for l in legs) * lots * lot_size
    return brokerage * len(legs) + prem_slip


def _exit_costs(legs: list[dict], quotes: dict[str, float], lots: int, lot_size: int,
                opt: dict) -> float:
    slip = float(opt["slippage_pct_premium"]) / 100.0
    brokerage = float(opt["brokerage_per_leg"])
    prem_slip = sum((quotes.get(f"{l['strike']}{l['opt_type']}") or 0.0) * slip for l in legs) \
        * lots * lot_size
    return brokerage * len(legs) + prem_slip


# ---------------------------------------------------------------------------
def open_trade(signal_id: int, lots: int) -> OptionTrade:
    """Fills every leg at its LIVE quote right now (never the stale signal quote)."""
    settings = get_settings()
    opt = settings["options"]
    adapter = active_adapter()
    if lots <= 0:
        raise ValueError("lots must be positive")

    with db_session() as s:
        sig = s.get(OptionSignal, signal_id)
        if sig is None or not sig.entry_ok:
            raise ValueError("signal not found or entry-blocked")

    expiry = date.fromisoformat(sig.expiry)
    items = [(l["strike"], l["opt_type"]) for l in sig.legs]
    quotes = adapter.get_option_quotes(sig.underlying, expiry, items)
    spot = adapter.get_ltp([sig.underlying]).get(sig.underlying, 0.0)

    legs = []
    credit = 0.0
    for l in sig.legs:
        q = quotes.get(f"{l['strike']}{l['opt_type']}")
        if q is None:
            raise BrokerError(f"no live quote for {l['strike']}{l['opt_type']}")
        legs.append({"side": l["side"], "opt_type": l["opt_type"],
                     "strike": l["strike"], "entry_price": q})
        credit += q if l["side"] == "sell" else -q
    if credit <= 0:
        raise ValueError("live net credit is non-positive — refusing to open")

    width = float(sig.max_loss) + float(sig.credit)  # wing width from the signal
    max_loss = max(width - credit, 0.05)
    lot_size = int(opt["lot_size"])
    margin = max_loss * lot_size * lots
    shorts = {l["opt_type"]: l["strike"] for l in legs if l["side"] == "sell"}
    entry_costs = _entry_costs(legs, lots, lot_size, opt)

    with db_session() as s:
        trade = OptionTrade(
            signal_id=signal_id, underlying=sig.underlying, strategy=sig.strategy,
            expiry=sig.expiry, lots=lots, lot_size=lot_size, legs=legs,
            net_credit=round(credit, 2), max_loss=round(max_loss, 2),
            margin_est=round(margin, 2),
            breakevens=sig.breakevens, short_strikes=shorts,
            spot_entry=round(spot, 2), costs=round(entry_costs, 2),
            broker_source=adapter.name, entry_time=datetime.utcnow(),
        )
        s.add(trade)
        s.flush()
        s.refresh(trade)
    log.info("paper OPTION SELL %s %s x%d lots credit %.1f", sig.strategy, sig.expiry, lots, credit)
    return trade


def _close(session, trade: OptionTrade, exit_debit: float, reason: str,
           spot: float | None, quotes: dict[str, float], opt: dict) -> None:
    exit_costs = _exit_costs(trade.legs, quotes, trade.lots, trade.lot_size, opt)
    gross = (trade.net_credit - exit_debit) * trade.lot_size * trade.lots
    trade.status = "closed"
    trade.exit_time = datetime.utcnow()
    trade.exit_debit = round(exit_debit, 2)
    trade.exit_reason = reason
    trade.spot_exit = round(spot, 2) if spot else None
    trade.costs = round(trade.costs + exit_costs, 2)
    trade.pnl = round(gross - trade.costs, 2)
    trade.return_on_margin_pct = round(trade.pnl / trade.margin_est * 100, 2) \
        if trade.margin_est > 0 else None
    log.info("paper OPTION CLOSE #%d %s pnl %.0f (%s)", trade.id, trade.strategy, trade.pnl, reason)


def _intrinsic_quotes(trade: OptionTrade, spot: float) -> dict[str, float]:
    out = {}
    for l in trade.legs:
        k = float(l["strike"])
        v = max(spot - k, 0.0) if l["opt_type"] == "CE" else max(k - spot, 0.0)
        out[f"{l['strike']}{l['opt_type']}"] = round(v, 2)
    return out


def manual_exit(trade_id: int) -> OptionTrade:
    settings = get_settings()
    opt = settings["options"]
    adapter = active_adapter()
    with db_session() as s:
        trade = s.get(OptionTrade, trade_id)
        if trade is None or trade.status != "open":
            raise ValueError("trade not found or already closed")
        expiry = date.fromisoformat(trade.expiry)
        items = [(l["strike"], l["opt_type"]) for l in trade.legs]
        quotes = adapter.get_option_quotes(trade.underlying, expiry, items)
        debit = _net_debit(trade.legs, quotes)
        if debit is None:
            raise BrokerError("missing live quotes for one or more legs")
        spot = adapter.get_ltp([trade.underlying]).get(trade.underlying)
        _close(s, trade, debit, "Manual Exit", spot, quotes, opt)
        s.flush()
        s.refresh(trade)
    return trade


# ---------------------------------------------------------------------------
def check_exits_once() -> dict:
    settings = get_settings()
    opt = settings["options"]
    adapter = active_adapter()
    with db_session() as s:
        open_trades = list(s.execute(
            select(OptionTrade).where(OptionTrade.status == "open")
        ).scalars())
        if not open_trades:
            return {"checked": 0, "closed": 0}

        closed = 0
        now = datetime.now(IST)
        try:
            spot = adapter.get_ltp([opt["underlying"]]).get(opt["underlying"])
        except BrokerError:
            spot = None

        for t in open_trades:
            expiry = date.fromisoformat(t.expiry)
            expired = now.date() > expiry or (now.date() == expiry and now.time() >= EXPIRY_CUTOFF)

            if expired:
                if spot is None:
                    continue
                quotes = _intrinsic_quotes(t, spot)
                debit = _net_debit(t.legs, quotes) or 0.0
                _close(s, t, debit, "Expiry Settlement", spot, quotes, opt)
                closed += 1
                continue

            items = [(l["strike"], l["opt_type"]) for l in t.legs]
            try:
                quotes = adapter.get_option_quotes(t.underlying, expiry, items)
            except BrokerError as e:
                log.warning("option MTM quotes failed for #%d: %s", t.id, e)
                continue
            debit = _net_debit(t.legs, quotes)
            if debit is None:
                continue

            pnl_pts = t.net_credit - debit  # per share, pre-costs
            max_profit_pts = t.net_credit
            take = float(opt["profit_take_pct_of_max"]) / 100.0
            stop_mult = float(opt["stop_loss_mult_of_credit"])

            if pnl_pts >= take * max_profit_pts:
                _close(s, t, debit, "Profit Target", spot, quotes, opt)
                closed += 1
            elif pnl_pts <= -stop_mult * t.net_credit:
                _close(s, t, debit, "Stop Loss", spot, quotes, opt)
                closed += 1
            elif opt.get("exit_on_short_strike_breach") and spot is not None:
                pe, ce = t.short_strikes.get("PE"), t.short_strikes.get("CE")
                if (pe and spot <= pe) or (ce and spot >= ce):
                    _close(s, t, debit, "Strike Breach", spot, quotes, opt)
                    closed += 1
        return {"checked": len(open_trades), "closed": closed}


def positions_snapshot() -> list[dict]:
    settings = get_settings()
    opt = settings["options"]
    adapter = active_adapter()
    with db_session() as s:
        open_trades = list(s.execute(
            select(OptionTrade).where(OptionTrade.status == "open").order_by(OptionTrade.id.desc())
        ).scalars())
    if not open_trades:
        return []
    try:
        spot = adapter.get_ltp([opt["underlying"]]).get(opt["underlying"])
    except BrokerError:
        spot = None
    out = []
    for t in open_trades:
        expiry = date.fromisoformat(t.expiry)
        items = [(l["strike"], l["opt_type"]) for l in t.legs]
        try:
            quotes = adapter.get_option_quotes(t.underlying, expiry, items)
        except BrokerError:
            quotes = {}
        debit = _net_debit(t.legs, quotes)
        legs_live = [
            {**l, "ltp": quotes.get(f"{l['strike']}{l['opt_type']}")} for l in t.legs
        ]
        upnl = pct = None
        if debit is not None:
            upnl = round((t.net_credit - debit) * t.lot_size * t.lots - t.costs, 2)
            pct = round(upnl / t.margin_est * 100, 2) if t.margin_est > 0 else None
        out.append({
            "id": t.id, "strategy": t.strategy, "expiry": t.expiry, "lots": t.lots,
            "lot_size": t.lot_size, "legs": legs_live, "net_credit": t.net_credit,
            "max_loss": t.max_loss, "margin_est": t.margin_est,
            "breakevens": t.breakevens, "short_strikes": t.short_strikes,
            "entry_time": t.entry_time.isoformat(), "spot_entry": t.spot_entry,
            "spot": spot, "cost_to_close": round(debit, 2) if debit is not None else None,
            "unrealized_pnl": upnl, "return_on_margin_pct": pct,
            "profit_target_pnl": round(float(settings["options"]["profit_take_pct_of_max"]) / 100
                                       * t.net_credit * t.lot_size * t.lots, 0),
        })
    return out


def weekly_performance() -> dict:
    """Closed option trades bucketed by ISO week of exit → weekly return on
    margin vs the configured 2–3% target."""
    settings = get_settings()
    target = float(settings["options"]["weekly_roc_target_pct"])
    with db_session() as s:
        closed = list(s.execute(
            select(OptionTrade).where(OptionTrade.status == "closed").order_by(OptionTrade.exit_time)
        ).scalars())
    weeks: dict[str, dict] = {}
    for t in closed:
        iso = t.exit_time.isocalendar()
        key = f"{iso[0]}-W{iso[1]:02d}"
        w = weeks.setdefault(key, {"week": key, "trades": 0, "pnl": 0.0, "margin": 0.0, "wins": 0})
        w["trades"] += 1
        w["pnl"] += t.pnl or 0.0
        w["margin"] += t.margin_est
        w["wins"] += 1 if (t.pnl or 0) > 0 else 0
    rows = []
    for key in sorted(weeks):
        w = weeks[key]
        ret = round(w["pnl"] / w["margin"] * 100, 2) if w["margin"] > 0 else None
        rows.append({**w, "pnl": round(w["pnl"], 2), "margin": round(w["margin"], 2),
                     "return_on_margin_pct": ret, "target_met": ret is not None and ret >= target})
    total_pnl = sum(t.pnl or 0 for t in closed)
    total_margin = sum(t.margin_est for t in closed)
    wins = sum(1 for t in closed if (t.pnl or 0) > 0)
    return {
        "weekly_target_pct": target,
        "weeks": rows,
        "overall": {
            "trades": len(closed),
            "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
            "net_pnl": round(total_pnl, 2),
            "avg_return_on_margin_pct": round(total_pnl / total_margin * 100, 2) if total_margin else None,
        },
    }
