"""Risk-adjusted metrics + portfolio-equity accounting (Phase 1)."""
from __future__ import annotations

from app.backtest import metrics


def _curve(rets: list[float], start: float = 100_000.0) -> list[dict]:
    eq, out = start, []
    for i, r in enumerate(rets):
        eq *= (1 + r)
        out.append({"t": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", "equity": round(eq, 2)})
    return out


def test_sharpe_and_sortino_from_daily_curve():
    # deterministic small positive drift with symmetric noise
    rets = [0.001 if i % 2 == 0 else -0.0005 for i in range(200)]
    m = metrics.compute_metrics([{"pnl": 1, "exit_time": "2024-06-01"}], 100_000,
                                daily_equity=_curve(rets))
    assert m["sharpe"] is not None and m["sharpe"] > 0
    assert m["sortino"] is not None
    assert m["cagr_pct"] is not None


def test_deflated_sharpe_penalises_many_trials():
    """More configs tested → lower deflated Sharpe for the SAME returns.

    Uses a MODEST-Sharpe series (~0.4 annualised) so the deflation is visible; a
    strong signal correctly saturates the DSR at ~1.0 for any trial count."""
    rets = [0.004 if i % 2 == 0 else -0.0038 for i in range(250)]  # ann. Sharpe ≈ 0.4
    curve = _curve(rets)
    dsr_1 = metrics.compute_metrics([{"pnl": 1, "exit_time": "2024-06-01"}], 100_000,
                                    daily_equity=curve, n_trials=1)["deflated_sharpe"]
    dsr_50 = metrics.compute_metrics([{"pnl": 1, "exit_time": "2024-06-01"}], 100_000,
                                     daily_equity=curve, n_trials=50)["deflated_sharpe"]
    assert dsr_1 is not None and dsr_50 is not None
    assert dsr_50 < dsr_1, f"deflated Sharpe must drop as trials rise ({dsr_50} !< {dsr_1})"
    assert 0.0 <= dsr_50 <= 1.0 and 0.0 <= dsr_1 <= 1.0


def test_deflated_sharpe_is_a_probability_not_a_ratio():
    rets = [0.001] * 100 + [-0.0005] * 100
    dsr = metrics.compute_metrics([{"pnl": 1, "exit_time": "2024-06-01"}], 100_000,
                                  daily_equity=_curve(rets), n_trials=10)["deflated_sharpe"]
    assert dsr is None or 0.0 <= dsr <= 1.0


def test_benchmark_passed_through():
    bm = {"return_pct": 12.0, "cagr_pct": 11.5, "sharpe": 0.9}
    m = metrics.compute_metrics([{"pnl": 100, "r_multiple": 1.0, "exit_time": "2024-06-01"}],
                                100_000, benchmark=bm)
    assert m["benchmark"] == bm


def test_by_year_breakdown():
    trades = [
        {"pnl": 500, "r_multiple": 2.0, "exit_time": "2024-03-01", "holding_days": 3},
        {"pnl": -200, "r_multiple": -1.0, "exit_time": "2024-09-01", "holding_days": 5},
        {"pnl": 300, "r_multiple": 1.5, "exit_time": "2025-02-01", "holding_days": 4},
    ]
    m = metrics.compute_metrics(trades, 100_000)
    years = {y["year"]: y for y in m["by_year"]}
    assert years["2024"]["trades"] == 2 and years["2024"]["pnl"] == 300.0
    assert years["2025"]["trades"] == 1 and years["2025"]["win_rate"] == 100.0


def test_live_analytics_call_unchanged_shape():
    """Live paper analytics calls compute_metrics(trades, capital) with no daily
    curve — must still return the same core fields it always did."""
    m = metrics.compute_metrics([{"pnl": 100, "r_multiple": 1.0, "exit_time": "2024-06-01"}],
                                100_000)
    for k in ("trades", "win_rate", "profit_factor", "expectancy_r", "net_pnl",
              "max_drawdown_pct", "total_return_pct", "equity_curve"):
        assert k in m


def test_empty_trades_returns_benchmark():
    m = metrics.compute_metrics([], 100_000, benchmark={"return_pct": 5.0})
    assert m["trades"] == 0 and m["benchmark"] == {"return_pct": 5.0}


def test_inv_norm_matches_known_quantiles():
    # Φ⁻¹(0.975) ≈ 1.95996
    assert abs(metrics._inv_norm(0.975) - 1.95996) < 1e-3
    assert abs(metrics._inv_norm(0.5)) < 1e-6
