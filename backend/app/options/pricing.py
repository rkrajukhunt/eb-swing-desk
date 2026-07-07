"""Deterministic option math — Black-Scholes (European, index options).

Everything the option-selling engine shows (credit, delta, expected move,
probability of profit, breakevens) is computed here with textbook formulas.
No LLM, no estimates without a formula.
"""
from __future__ import annotations

from math import exp, log, sqrt
from statistics import NormalDist

_N = NormalDist()


def _d1_d2(spot: float, strike: float, t: float, iv: float, r: float) -> tuple[float, float]:
    d1 = (log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * sqrt(t))
    return d1, d1 - iv * sqrt(t)


def bs_price(spot: float, strike: float, t: float, iv: float, r: float, opt_type: str) -> float:
    """European Black-Scholes price. t in years, iv/r as decimals, opt_type CE|PE."""
    if t <= 0 or iv <= 0:
        intrinsic = max(spot - strike, 0.0) if opt_type == "CE" else max(strike - spot, 0.0)
        return intrinsic
    d1, d2 = _d1_d2(spot, strike, t, iv, r)
    if opt_type == "CE":
        return spot * _N.cdf(d1) - strike * exp(-r * t) * _N.cdf(d2)
    return strike * exp(-r * t) * _N.cdf(-d2) - spot * _N.cdf(-d1)


def bs_delta(spot: float, strike: float, t: float, iv: float, r: float, opt_type: str) -> float:
    if t <= 0 or iv <= 0:
        if opt_type == "CE":
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    d1, _ = _d1_d2(spot, strike, t, iv, r)
    return _N.cdf(d1) if opt_type == "CE" else _N.cdf(d1) - 1.0


def implied_vol(price: float, spot: float, strike: float, t: float, r: float,
                opt_type: str, lo: float = 0.01, hi: float = 3.0) -> float | None:
    """Bisection IV solver. Returns None when the price is outside no-arbitrage
    bounds (deep ITM/illiquid junk quotes)."""
    if t <= 0 or price <= 0:
        return None
    if bs_price(spot, strike, t, lo, r, opt_type) > price:
        return None
    if bs_price(spot, strike, t, hi, r, opt_type) < price:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if bs_price(spot, strike, t, mid, r, opt_type) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def prob_above(spot: float, level: float, t: float, iv: float, r: float) -> float:
    """Risk-neutral P(S_T > level) under lognormal dynamics."""
    if t <= 0 or iv <= 0:
        return 1.0 if spot > level else 0.0
    z = (log(spot / level) + (r - 0.5 * iv * iv) * t) / (iv * sqrt(t))
    return _N.cdf(z)


def expected_move_from_straddle(straddle_price: float) -> float:
    """Market-implied ±1σ-ish expected move ≈ ATM straddle price.

    The ATM straddle is the cleanest market estimate of the expected absolute
    move to expiry (≈ 0.8σ of the lognormal distribution); using it directly is
    the standard practitioner convention."""
    return straddle_price


def expected_move_from_iv(spot: float, iv: float, t: float) -> float:
    """Statistical 1σ move: S·σ·√t. Used as fallback when the straddle quote
    is unavailable."""
    return spot * iv * sqrt(max(t, 0.0))
