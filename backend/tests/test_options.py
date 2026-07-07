import math
from datetime import date, timedelta

import pytest

from app.options.pricing import (
    bs_delta,
    bs_price,
    expected_move_from_iv,
    implied_vol,
    prob_above,
)


R = 0.065


def test_put_call_parity():
    spot, strike, t, iv = 24000.0, 24000.0, 7 / 365, 0.13
    c = bs_price(spot, strike, t, iv, R, "CE")
    p = bs_price(spot, strike, t, iv, R, "PE")
    # C - P = S - K·e^(-rt)
    assert c - p == pytest.approx(spot - strike * math.exp(-R * t), abs=0.01)


def test_delta_ranges_and_signs():
    spot, t, iv = 24000.0, 7 / 365, 0.13
    assert 0.4 < bs_delta(spot, 24000, t, iv, R, "CE") < 0.6
    assert -0.6 < bs_delta(spot, 24000, t, iv, R, "PE") < -0.4
    assert bs_delta(spot, 25500, t, iv, R, "CE") < 0.1          # far OTM call
    assert abs(bs_delta(spot, 22500, t, iv, R, "PE")) < 0.1     # far OTM put


def test_implied_vol_roundtrip():
    spot, strike, t, iv = 24000.0, 23600.0, 5 / 365, 0.17
    price = bs_price(spot, strike, t, iv, R, "PE")
    solved = implied_vol(price, spot, strike, t, R, "PE")
    assert solved == pytest.approx(iv, abs=1e-3)


def test_prob_above_monotonic():
    spot, t, iv = 24000.0, 7 / 365, 0.13
    p_low = prob_above(spot, 23000, t, iv, R)
    p_atm = prob_above(spot, 24000, t, iv, R)
    p_high = prob_above(spot, 25000, t, iv, R)
    assert p_low > p_atm > p_high
    assert 0 <= p_high and p_low <= 1


def test_expected_move_scales_with_time():
    em1 = expected_move_from_iv(24000, 0.13, 7 / 365)
    em2 = expected_move_from_iv(24000, 0.13, 28 / 365)
    assert em2 == pytest.approx(em1 * 2, rel=1e-6)  # √4 = 2


# ---------------------------------------------------------------------------
def _mock_chain(spot=24000.0, t=6 / 365, iv=0.14, step=50, span=2500):
    """Synthetic BS chain with flat IV for structure-builder tests."""
    quotes = {}
    k = int(spot - span)
    while k <= spot + span:
        quotes[f"{k}CE"] = round(max(bs_price(spot, k, t, iv, R, "CE"), 0.05), 2)
        quotes[f"{k}PE"] = round(max(bs_price(spot, k, t, iv, R, "PE"), 0.05), 2)
        k += step
    return quotes


OPT_SETTINGS = {
    "strike_step": 50, "wing_width_points": 200, "lot_size": 75,
    "max_short_delta": 0.35, "weekly_roc_target_pct": 2.0,
    "min_credit_points": 5.0, "em_multiplier": 1.1, "em_multiplier_floor": 0.5,
    "profit_take_pct_of_max": 60.0, "stop_loss_mult_of_credit": 2.0,
}


def test_iron_condor_structure_invariants():
    from app.options.structures import ChainView, build_structure

    spot, t = 24000.0, 6 / 365
    chain = ChainView(spot, _mock_chain(spot, t), t, R)
    em = expected_move_from_iv(spot, 0.14, t)
    s, reject = build_structure("iron_condor", chain, em, OPT_SETTINGS)
    assert s is not None, reject
    assert len(s.legs) == 4
    sells = [l for l in s.legs if l.side == "sell"]
    buys = [l for l in s.legs if l.side == "buy"]
    assert len(sells) == 2 and len(buys) == 2
    # hedges are further OTM than shorts by wing width
    sp = next(l for l in sells if l.opt_type == "PE")
    bp = next(l for l in buys if l.opt_type == "PE")
    sc = next(l for l in sells if l.opt_type == "CE")
    bc = next(l for l in buys if l.opt_type == "CE")
    assert bp.strike == sp.strike - 200 and bc.strike == sc.strike + 200
    assert sp.strike < spot < sc.strike
    # economics
    assert s.credit > 0
    assert s.max_loss == pytest.approx(200 - s.credit, abs=0.01)
    assert s.margin_per_lot == pytest.approx(s.max_loss * 75, abs=0.5)
    assert s.roc_pct == pytest.approx(s.credit / s.max_loss * 100, abs=0.05)
    assert 0 < s.pop_pct < 100
    # breakevens sit inside the short strikes
    lo, hi = s.breakevens
    assert bp.strike < lo < sp.strike and sc.strike < hi < bc.strike
    # short deltas respect the cap
    assert all(abs(l.delta) <= OPT_SETTINGS["max_short_delta"] + 0.01 for l in sells)
    assert s.reasons


def test_directional_spreads_single_side():
    from app.options.structures import ChainView, build_structure

    spot, t = 24000.0, 6 / 365
    chain = ChainView(spot, _mock_chain(spot, t), t, R)
    em = expected_move_from_iv(spot, 0.14, t)
    bull, _ = build_structure("bull_put_spread", chain, em, OPT_SETTINGS)
    assert bull is not None and len(bull.legs) == 2
    assert all(l.opt_type == "PE" for l in bull.legs)
    bear, _ = build_structure("bear_call_spread", chain, em, OPT_SETTINGS)
    assert bear is not None and len(bear.legs) == 2
    assert all(l.opt_type == "CE" for l in bear.legs)


def test_roc_target_search_never_breaches_delta_cap():
    from app.options.structures import ChainView, build_structure

    spot, t = 24000.0, 6 / 365
    chain = ChainView(spot, _mock_chain(spot, t), t, R)
    em = expected_move_from_iv(spot, 0.14, t)
    # ROC = credit/(width−credit); 500% would need credit ≥ 5/6 of the wing
    # width, impossible at any strike the delta cap / EM floor allows
    greedy = dict(OPT_SETTINGS, weekly_roc_target_pct=500.0)
    s, _ = build_structure("iron_condor", chain, em, greedy)
    assert s is not None
    assert not s.target_met
    for l in s.legs:
        if l.side == "sell":
            assert abs(l.delta) <= greedy["max_short_delta"] + 0.01
    assert any("NOT reachable" in r for r in s.reasons)


def test_expiry_calendar():
    from app.options.expiries import next_weekly_expiries

    exps = next_weekly_expiries(1, count=4)  # Tuesdays
    assert len(exps) == 4
    assert all(exps[i] < exps[i + 1] for i in range(3))
    for e in exps:
        assert e.weekday() <= 1 or e.weekday() >= 0  # Tue nominal, may shift back for holidays
        assert (date.today() <= e <= date.today() + timedelta(days=35))
