import pandas as pd
import pytest

from app.config import DEFAULT_SETTINGS
from app.engine.signals import apply_costs, build_levels, position_size


def make_row(**over):
    base = {
        "close": 100.0, "atr14": 2.0, "swing_low": 95.0, "high_52w": 130.0,
    }
    base.update(over)
    return pd.Series(base)


def test_stop_is_tighter_of_atr_and_swing_low():
    settings = dict(DEFAULT_SETTINGS)
    # ATR stop = 100 - 1.5*2 = 97 → tighter (higher) than swing low 95
    levels, _ = build_levels(make_row(), "trend_pullback", settings)
    assert levels.stop_loss == 97.0
    # swing low above ATR stop → swing low wins
    levels, _ = build_levels(make_row(swing_low=98.0), "trend_pullback", settings)
    assert levels.stop_loss == 98.0


def test_targets_are_rr_multiples():
    levels, _ = build_levels(make_row(), "trend_pullback", DEFAULT_SETTINGS)
    risk = levels.entry - levels.stop_loss
    assert levels.target_2r == pytest.approx(levels.entry + 2 * risk)
    assert levels.target_3r == pytest.approx(levels.entry + 3 * risk)


def test_rejects_poor_headroom_rr():
    # 52w high barely above entry → effective R:R < 1.5 → reject
    levels, reason = build_levels(make_row(high_52w=101.0), "trend_pullback", DEFAULT_SETTINGS)
    assert levels is None
    assert "R:R" in reason


def test_breakout_skips_headroom_gate():
    levels, _ = build_levels(make_row(high_52w=101.0), "breakout", DEFAULT_SETTINGS)
    assert levels is not None


def test_rejects_excessive_risk_pct():
    levels, reason = build_levels(
        make_row(atr14=10.0, swing_low=50.0), "trend_pullback", DEFAULT_SETTINGS
    )
    assert levels is None
    assert "risk" in reason


def test_position_size_math():
    # 500000 * 1% = 5000 risk budget; risk/share 3 → 1666 shares
    assert position_size(500_000, 1.0, 100.0, 97.0) == 1666
    # capped by notional: 500000/4000 = 125 shares
    assert position_size(500_000, 1.0, 4000.0, 3990.0) == 125
    assert position_size(500_000, 1.0, 100.0, 100.0) == 0


def test_apply_costs_reduces_pnl():
    settings = dict(DEFAULT_SETTINGS)
    net, costs = apply_costs(100.0, 110.0, 100, settings)
    gross = 10.0 * 100
    assert net < gross
    assert costs > 0
    assert net == pytest.approx(gross - costs)


# ---------------------------------------------------------------------------
# LLM output validation — the integrity firewall
# ---------------------------------------------------------------------------
class FakeSignal:
    def __init__(self, symbol, entry=100.0):
        self.symbol = symbol
        self.entry = entry
        self.stop_loss = entry * 0.97
        self.target_2r = entry * 1.06
        self.target_3r = entry * 1.09
        self.risk_reward = 2.0
        self.pct_risk = 3.0
        self.composite_score = 70.0
        self.indicators = {}
        self.strategy = "breakout"
        self.llm_rank = None
        self.llm_conviction = None
        self.llm_rationale = None
        self.llm_conflicts = []
        self.llm_regime_note = None


def _echo(sig, rank, **over):
    d = {
        "id": sig.symbol, "rank": rank, "conviction": "high",
        "entry": sig.entry, "stop_loss": sig.stop_loss,
        "target_2r": sig.target_2r, "target_3r": sig.target_3r,
        "risk_reward": sig.risk_reward,
        "rationale": "Strong setup.", "conflicts": [], "regime_note": "ok",
    }
    d.update(over)
    return d


def test_llm_tampering_detected_and_engine_values_kept():
    from app.llm.ranker import validate_and_apply

    sigs = [FakeSignal("TCS"), FakeSignal("INFY")]
    data = {"ranked": [
        _echo(sigs[0], 1, entry=999.0),          # tampered entry
        _echo(sigs[1], 2),
    ]}
    issues = validate_and_apply(data, sigs, 20)
    kinds = [i["type"] for i in issues]
    assert "price_tampering" in kinds
    assert sigs[0].entry == 100.0  # engine value untouched
    assert sigs[0].llm_rank == 1 and sigs[1].llm_rank == 2


def test_llm_hallucinated_symbol_dropped_and_missing_appended():
    from app.llm.ranker import validate_and_apply

    sigs = [FakeSignal("TCS"), FakeSignal("INFY"), FakeSignal("SBIN")]
    data = {"ranked": [
        _echo(sigs[0], 1),
        {"id": "FAKECO", "rank": 2, "entry": 1, "stop_loss": 1, "target_2r": 1,
         "target_3r": 1, "risk_reward": 1, "rationale": "?", "conflicts": []},
        # INFY and SBIN dropped by the model
    ]}
    issues = validate_and_apply(data, sigs, 20)
    kinds = [i["type"] for i in issues]
    assert "hallucinated_symbol" in kinds
    assert kinds.count("dropped_by_llm") == 2
    # dropped candidates appended after ranked ones
    assert sigs[0].llm_rank == 1
    assert {sigs[1].llm_rank, sigs[2].llm_rank} == {2, 3}


def test_llm_rationale_sanitized():
    from app.llm.ranker import validate_and_apply

    sigs = [FakeSignal("TCS")]
    data = {"ranked": [_echo(sigs[0], 1, rationale='<script>alert(1)</script>Solid trend.')]}
    validate_and_apply(data, sigs, 20)
    assert "<script>" not in sigs[0].llm_rationale
    assert "Solid trend." in sigs[0].llm_rationale
