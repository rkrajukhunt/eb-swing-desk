"""Defined-risk option-selling structure builder — the deterministic algo.

Research-backed rules implemented (all parameters tunable in Settings):

  WHICH structure — driven by the same NIFTY regime module the swing screener
  uses (single source of market truth):
    bullish  → bull put spread   (sell OTM put,  buy further-OTM put hedge)
    bearish  → bear call spread  (sell OTM call, buy further-OTM call hedge)
    neutral  → iron condor       (both credit spreads at once)
  Every structure BUYS a hedge leg — max loss is capped by construction; naked
  writing is deliberately not offered.

  WHERE the short strike goes — start just beyond `em_multiplier` × the
  market-implied expected move (ATM straddle price), then tighten step by step
  until the trade's return-on-margin meets the weekly ROC target. Two hard
  bounds stop the search: the short-strike delta cap (`max_short_delta`) and
  the EM-multiple floor. If the target is unreachable inside those bounds, the
  best compliant structure is returned flagged `target_met = False` — the algo
  never silently takes on extra risk to chase yield.

  Sanity gates: minimum total credit, positive max loss, IV floor (checked by
  the engine), DTE entry window (checked by the engine).

All prices come from live quotes; IV and delta are solved from those quotes
with Black-Scholes (pricing.py). Margin for defined-risk structures is
approximated as max loss per lot (broker SPAN for hedged spreads is close to,
and never more meaningful than, this number for sizing purposes).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pricing import bs_delta, implied_vol, prob_above

STRATEGY_LABELS = {
    "bull_put_spread": "Bull Put Spread",
    "bear_call_spread": "Bear Call Spread",
    "iron_condor": "Iron Condor",
}


@dataclass
class Leg:
    side: str        # sell | buy
    opt_type: str    # CE | PE
    strike: int
    price: float     # live quote at build time
    delta: float | None = None
    iv_pct: float | None = None


@dataclass
class Structure:
    strategy: str
    legs: list[Leg]
    credit: float                 # net credit per share (points)
    max_profit: float             # per share
    max_loss: float               # per share
    margin_per_lot: float         # ₹, ≈ max loss × lot size
    roc_pct: float                # max_profit / margin, as % (this cycle ≤ 1 week)
    pop_pct: float                # probability of profit (risk-neutral, BS)
    breakevens: list[float]
    target_met: bool
    reasons: list[str] = field(default_factory=list)


class ChainView:
    """Quote access + solved IV/delta for one expiry. `quotes` maps
    '24500PE' → LTP."""

    def __init__(self, spot: float, quotes: dict[str, float], t_years: float, r: float):
        self.spot = spot
        self.quotes = quotes
        self.t = t_years
        self.r = r

    def price(self, strike: int, opt_type: str) -> float | None:
        return self.quotes.get(f"{strike}{opt_type}")

    def leg(self, side: str, opt_type: str, strike: int) -> Leg | None:
        p = self.price(strike, opt_type)
        if p is None or p <= 0:
            return None
        iv = implied_vol(p, self.spot, float(strike), self.t, self.r, opt_type)
        delta = bs_delta(self.spot, float(strike), self.t, iv, self.r, opt_type) if iv else None
        return Leg(side=side, opt_type=opt_type, strike=strike, price=p,
                   delta=round(delta, 3) if delta is not None else None,
                   iv_pct=round(iv * 100, 1) if iv else None)


def _round_step(x: float, step: int, direction: str) -> int:
    import math

    return int((math.floor if direction == "down" else math.ceil)(x / step) * step)


def _atm_iv(chain: ChainView, atm_strike: int) -> float | None:
    ivs = []
    for t_ in ("CE", "PE"):
        p = chain.price(atm_strike, t_)
        if p:
            iv = implied_vol(p, chain.spot, float(atm_strike), chain.t, chain.r, t_)
            if iv:
                ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else None


def _spread(chain: ChainView, opt_type: str, short_strike: int, width: int,
            lot_size: int) -> tuple[list[Leg], float] | None:
    """One credit spread: sell short_strike, buy hedge `width` points further OTM."""
    hedge_strike = short_strike - width if opt_type == "PE" else short_strike + width
    short = chain.leg("sell", opt_type, short_strike)
    hedge = chain.leg("buy", opt_type, hedge_strike)
    if short is None or hedge is None:
        return None
    credit = short.price - hedge.price
    if credit <= 0:
        return None
    return [short, hedge], credit


def build_structure(
    strategy: str,
    chain: ChainView,
    expected_move: float,
    settings_opt: dict,
) -> tuple[Structure | None, str]:
    """Builds the requested structure with the ROC-target strike search.
    Returns (structure, reject_reason)."""
    step = int(settings_opt["strike_step"])
    width = int(settings_opt["wing_width_points"])
    lot = int(settings_opt["lot_size"])
    max_delta = float(settings_opt["max_short_delta"])
    roc_target = float(settings_opt["weekly_roc_target_pct"])
    min_credit = float(settings_opt["min_credit_points"])
    k_start = float(settings_opt["em_multiplier"])
    k_floor = float(settings_opt["em_multiplier_floor"])
    spot = chain.spot

    best: Structure | None = None
    k = k_start
    tried: list[str] = []

    while k >= k_floor - 1e-9:
        legs: list[Leg] = []
        credit = 0.0
        ok = True

        if strategy in ("bull_put_spread", "iron_condor"):
            sp = _round_step(spot - k * expected_move, step, "down")
            r = _spread(chain, "PE", sp, width, lot)
            if r is None:
                ok = False
            else:
                legs += r[0]
                credit += r[1]
        if ok and strategy in ("bear_call_spread", "iron_condor"):
            sc = _round_step(spot + k * expected_move, step, "up")
            r = _spread(chain, "CE", sc, width, lot)
            if r is None:
                ok = False
            else:
                legs += r[0]
                credit += r[1]

        if ok:
            short_deltas = [abs(l.delta) for l in legs if l.side == "sell" and l.delta is not None]
            delta_ok = all(d <= max_delta for d in short_deltas)
            # For an iron condor only one side can lose at expiry → max loss =
            # width − total credit (both credits cushion the losing side).
            max_loss = width - credit
            if max_loss <= 0:
                ok = False
            if ok and delta_ok and credit >= min_credit:
                margin = max_loss * lot
                roc = credit * lot / margin * 100
                pop = _pop(strategy, chain, legs, credit)
                s = Structure(
                    strategy=strategy, legs=legs, credit=round(credit, 2),
                    max_profit=round(credit, 2), max_loss=round(max_loss, 2),
                    margin_per_lot=round(margin, 2), roc_pct=round(roc, 2),
                    pop_pct=round(pop * 100, 1),
                    breakevens=_breakevens(strategy, legs, credit),
                    target_met=roc >= roc_target,
                    reasons=[],
                )
                tried.append(f"k={k:.1f}: short {'/'.join(str(l.strike) for l in legs if l.side=='sell')} "
                             f"credit {credit:.1f} ROC {roc:.2f}% POP {s.pop_pct}%")
                if best is None or s.roc_pct > best.roc_pct:
                    best = s
                if s.target_met:
                    s.reasons = _reasons(s, expected_move, k, settings_opt, tried)
                    return s, ""
            elif not delta_ok:
                tried.append(f"k={k:.1f}: short delta {max(short_deltas):.2f} > cap {max_delta} — stop tightening")
                break  # any tighter k only increases delta
            else:
                tried.append(f"k={k:.1f}: credit {credit:.1f} < min {min_credit}")
        k = round(k - 0.1, 4)

    if best is not None:
        best.reasons = _reasons(best, expected_move, None, settings_opt, tried)
        best.reasons.append(
            f"⚠ weekly ROC target {roc_target}% NOT reachable within the delta cap "
            f"{settings_opt['max_short_delta']} — best compliant structure returned instead"
        )
        return best, ""
    return None, "no structure satisfies credit/delta constraints (chain too thin or IV too low)"


def _pop(strategy: str, chain: ChainView, legs: list[Leg], credit: float) -> float:
    """Probability of profit from breakevens under BS lognormal (uses the
    short legs' solved IV)."""
    ivs = [l.iv_pct / 100 for l in legs if l.side == "sell" and l.iv_pct]
    iv = sum(ivs) / len(ivs) if ivs else 0.13
    shorts = {l.opt_type: l.strike for l in legs if l.side == "sell"}
    if strategy == "bull_put_spread":
        be = shorts["PE"] - credit
        return prob_above(chain.spot, be, chain.t, iv, chain.r)
    if strategy == "bear_call_spread":
        be = shorts["CE"] + credit
        return 1 - prob_above(chain.spot, be, chain.t, iv, chain.r)
    be_low = shorts["PE"] - credit
    be_high = shorts["CE"] + credit
    return max(prob_above(chain.spot, be_low, chain.t, iv, chain.r)
               - prob_above(chain.spot, be_high, chain.t, iv, chain.r), 0.0)


def _breakevens(strategy: str, legs: list[Leg], credit: float) -> list[float]:
    shorts = {l.opt_type: l.strike for l in legs if l.side == "sell"}
    if strategy == "bull_put_spread":
        return [round(shorts["PE"] - credit, 2)]
    if strategy == "bear_call_spread":
        return [round(shorts["CE"] + credit, 2)]
    return [round(shorts["PE"] - credit, 2), round(shorts["CE"] + credit, 2)]


def _reasons(s: Structure, em: float, k: float | None, opt: dict, tried: list[str]) -> list[str]:
    out = [
        f"expected move to expiry (ATM straddle) = ±{em:.0f} pts",
        f"short strikes placed beyond {'%.1f×' % k if k else 'best-ROC'} expected move "
        f"(search started at {opt['em_multiplier']}× EM, delta cap {opt['max_short_delta']})",
        f"hedge legs bought {opt['wing_width_points']} pts further OTM → max loss capped at "
        f"₹{s.margin_per_lot:,.0f}/lot by construction",
        f"credit {s.credit:.1f} pts → max profit ₹{s.max_profit * opt['lot_size']:,.0f}/lot; "
        f"ROC = credit/margin = {s.roc_pct}% for this weekly cycle",
        f"probability of profit (BS, from solved IV) ≈ {s.pop_pct}%",
        f"exits: take profit at {opt['profit_take_pct_of_max']}% of max, stop at "
        f"{opt['stop_loss_mult_of_credit']}× credit, breach of short strike, or expiry settlement",
    ]
    out += [f"search: {t}" for t in tried[-4:]]
    return out
