"""Option-selling signal engine — decides WHEN to sell and produces the
structure (WHAT to sell + WHAT hedge to buy), fully deterministic.

Entry gates (each produces an explicit block reason instead of a bad trade):
  * options module enabled
  * DTE inside [entry_dte_min, entry_dte_max]  (weekly cycle only)
  * ATM implied vol ≥ min_iv_pct               (premium worth selling)
  * a compliant structure exists (structures.build_structure)
Regime (shared with the swing module) picks the structure unless the user
forces one.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from ..adapters.base import BrokerAdapter, BrokerError
from ..database import db_session
from ..engine.scanner import get_current_regime
from ..models import OptionSignal
from ..services.settings_store import get_settings
from .expiries import dte_calendar_days, next_weekly_expiries
from .pricing import expected_move_from_iv, expected_move_from_straddle, implied_vol
from .structures import ChainView, build_structure

log = logging.getLogger(__name__)

REGIME_TO_STRATEGY = {
    "bullish": "bull_put_spread",
    "bearish": "bear_call_spread",
    "neutral": "iron_condor",
    "unknown": "iron_condor",
}


def _chain_items(spot: float, step: int, span_pct: float = 0.08) -> list[tuple[int, str]]:
    lo = int((spot * (1 - span_pct)) // step * step)
    hi = int((spot * (1 + span_pct)) // step * step + step)
    items = []
    for strike in range(lo, hi + 1, step):
        items.append((strike, "CE"))
        items.append((strike, "PE"))
    return items


def generate_signal(adapter: BrokerAdapter, force_strategy: str | None = None) -> int:
    """Generates and persists an OptionSignal; returns its id.
    Blocked entries are persisted too (entry_ok=False) so the UI can show WHY
    the algo is not selling right now."""
    settings = get_settings()
    opt = settings["options"]
    underlying = opt["underlying"]
    step = int(opt["strike_step"])
    r = float(opt["risk_free_rate_pct"]) / 100.0

    regime = get_current_regime(adapter, refresh=False)
    strategy = force_strategy if force_strategy in REGIME_TO_STRATEGY.values() \
        else REGIME_TO_STRATEGY.get(regime.label, "iron_condor")

    def _blocked(reason: str, **extra) -> int:
        with db_session() as s:
            sig = OptionSignal(
                underlying=underlying, expiry=extra.get("expiry", ""), dte=extra.get("dte", 0.0),
                spot=extra.get("spot", 0.0), regime=regime.label, strategy=strategy,
                entry_ok=False, entry_block_reason=reason,
                atm_iv_pct=extra.get("atm_iv_pct"), expected_move=extra.get("em"),
                reasons=[f"entry blocked: {reason}"],
            )
            s.add(sig)
            s.flush()
            return sig.id

    if not opt.get("enabled", True):
        return _blocked("options module disabled in settings")

    # Spot + expiry
    try:
        spot = adapter.get_ltp([underlying])[underlying]
    except (BrokerError, KeyError) as e:
        return _blocked(f"no live spot quote for {underlying}: {e}")

    try:
        expiries = adapter.get_option_expiries(underlying)
    except BrokerError:
        expiries = next_weekly_expiries(int(opt["expiry_weekday"]))
    if not expiries:
        return _blocked("no upcoming expiry found", spot=spot)
    expiry = expiries[0]
    dte = dte_calendar_days(expiry)
    t = max(dte, 0.25) / 365.0

    if dte < float(opt["entry_dte_min"]) or dte > float(opt["entry_dte_max"]):
        return _blocked(
            f"DTE {dte:.1f} outside entry window [{opt['entry_dte_min']}, {opt['entry_dte_max']}] — "
            f"wait for the weekly cycle", spot=spot, expiry=str(expiry), dte=dte)

    # Live chain
    try:
        quotes = adapter.get_option_quotes(underlying, expiry, _chain_items(spot, step))
    except BrokerError as e:
        return _blocked(f"option chain unavailable: {e}", spot=spot, expiry=str(expiry), dte=dte)
    if len(quotes) < 8:
        return _blocked("option chain too thin", spot=spot, expiry=str(expiry), dte=dte)

    chain = ChainView(spot, quotes, t, r)
    atm = int(round(spot / step) * step)

    # ATM IV + expected move (straddle preferred, IV-based fallback)
    ce, pe = chain.price(atm, "CE"), chain.price(atm, "PE")
    ivs = []
    for typ, p in (("CE", ce), ("PE", pe)):
        if p:
            iv = implied_vol(p, spot, float(atm), t, r, typ)
            if iv:
                ivs.append(iv)
    atm_iv = sum(ivs) / len(ivs) if ivs else None
    if ce and pe:
        em = expected_move_from_straddle(ce + pe)
    elif atm_iv:
        em = expected_move_from_iv(spot, atm_iv, t)
    else:
        return _blocked("cannot establish ATM IV / expected move", spot=spot,
                        expiry=str(expiry), dte=dte)
    atm_iv_pct = round(atm_iv * 100, 1) if atm_iv else None

    if atm_iv_pct is not None and atm_iv_pct < float(opt["min_iv_pct"]):
        return _blocked(
            f"ATM IV {atm_iv_pct}% < floor {opt['min_iv_pct']}% — premium too thin to sell",
            spot=spot, expiry=str(expiry), dte=dte, atm_iv_pct=atm_iv_pct, em=em)

    structure, reject = build_structure(strategy, chain, em, opt)
    if structure is None:
        return _blocked(reject, spot=spot, expiry=str(expiry), dte=dte,
                        atm_iv_pct=atm_iv_pct, em=em)

    # Sizing: lots from the capital allocation
    alloc = float(settings["capital"]) * float(opt["capital_allocation_pct"]) / 100.0
    lots = int(alloc // structure.margin_per_lot) if structure.margin_per_lot > 0 else 0
    structure.reasons.append(
        f"sizing: floor(capital {settings['capital']:,.0f} × {opt['capital_allocation_pct']}% "
        f"/ margin {structure.margin_per_lot:,.0f}) = {lots} lot(s) of {opt['lot_size']}"
    )

    # Chain snapshot ±5 strikes for the UI
    snapshot = []
    for k in range(atm - 5 * step, atm + 6 * step, step):
        row = {"strike": k, "ce": chain.price(k, "CE"), "pe": chain.price(k, "PE")}
        snapshot.append(row)

    with db_session() as s:
        sig = OptionSignal(
            underlying=underlying, expiry=str(expiry), dte=round(dte, 2), spot=round(spot, 2),
            regime=regime.label, strategy=structure.strategy, entry_ok=True,
            atm_iv_pct=atm_iv_pct, expected_move=round(em, 1),
            legs=[vars(l) for l in structure.legs],
            credit=structure.credit, max_profit=structure.max_profit,
            max_loss=structure.max_loss, margin_per_lot=structure.margin_per_lot,
            roc_pct=structure.roc_pct, pop_pct=structure.pop_pct,
            breakevens=structure.breakevens, lots_suggested=lots,
            target_met=structure.target_met, reasons=structure.reasons,
            chain_snapshot=snapshot,
        )
        s.add(sig)
        s.flush()
        return sig.id


def latest_signal() -> OptionSignal | None:
    with db_session() as s:
        return s.execute(
            select(OptionSignal).order_by(OptionSignal.id.desc()).limit(1)
        ).scalar_one_or_none()
