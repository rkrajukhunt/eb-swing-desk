"""Performance metrics shared by the backtester AND live paper analytics —
one formula set, so the two are directly comparable.

Two families of numbers:
  * Trade-based (win rate, expectancy R, profit factor) — from the trade list.
  * Return-based (CAGR, Sharpe, Sortino, max drawdown) — from a *daily* equity
    curve when one is supplied (the backtester marks concurrent positions to
    market daily; see backtest/engine.py). Live paper analytics has no daily
    curve, so it passes none and those fields fall back to the per-trade curve.

Risk-adjusted numbers are only meaningful out-of-sample and against a benchmark —
callers pass `benchmark` (NIFTY buy-and-hold over the same window) and `n_trials`
(how many configs were tested) so the Sharpe can be *deflated* for multiple
testing. See ALGORITHM_IMPROVEMENT_PLAN.md, Phase 1.
"""
from __future__ import annotations

import math
from datetime import date

TRADING_DAYS = 252


def _empty() -> dict:
    return {
        "trades": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
        "expectancy_r": None, "profit_factor": None, "avg_r": None,
        "max_drawdown_pct": None, "total_return_pct": None, "net_pnl": 0.0,
        "avg_holding_days": None, "cagr_pct": None, "sharpe": None,
        "sortino": None, "deflated_sharpe": None, "benchmark": None,
        "by_year": [], "equity_curve": [],
    }


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: list[float], ddof: int = 1) -> float:
    n = len(xs)
    if n <= ddof:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - ddof))


def _skew(xs: list[float]) -> float:
    n, s = len(xs), _std(xs, ddof=1)
    if n < 3 or s == 0:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 3 for x in xs) / n) / (s ** 3)


def _kurtosis(xs: list[float]) -> float:
    """Non-excess (normal ≈ 3)."""
    n, s = len(xs), _std(xs, ddof=1)
    if n < 4 or s == 0:
        return 3.0
    m = _mean(xs)
    return (sum((x - m) ** 4 for x in xs) / n) / (s ** 4)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inv_norm(p: float) -> float:
    """Acklam's rational approximation to the standard-normal quantile."""
    p = min(max(p, 1e-9), 1 - 1e-9)
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _daily_returns(curve: list[dict]) -> list[float]:
    rets: list[float] = []
    prev: float | None = None
    for pt in curve:
        eq = pt.get("equity")
        if eq is None or eq <= 0:
            continue
        if prev is not None and prev > 0:
            rets.append(eq / prev - 1.0)
        prev = eq
    return rets


def _sharpe(daily_rets: list[float]) -> float | None:
    if len(daily_rets) < 20:
        return None
    sd = _std(daily_rets)
    if sd == 0:
        return None
    return round(_mean(daily_rets) / sd * math.sqrt(TRADING_DAYS), 2)


def _sortino(daily_rets: list[float]) -> float | None:
    if len(daily_rets) < 20:
        return None
    downside = [r for r in daily_rets if r < 0]
    dd = _std(downside, ddof=0) if downside else 0.0
    if dd == 0:
        return None
    return round(_mean(daily_rets) / dd * math.sqrt(TRADING_DAYS), 2)


def _deflated_sharpe(daily_rets: list[float], observed_sharpe: float | None,
                     n_trials: int) -> float | None:
    """Bailey & López de Prado (2014): probability the true (annualised) Sharpe is
    > 0 after correcting for having selected the best of `n_trials` configs and for
    non-normal returns. In [0,1]; treat < 0.95 as 'not yet convincing'."""
    n = len(daily_rets)
    if observed_sharpe is None or n < 30 or n_trials < 1:
        return None
    sr = observed_sharpe / math.sqrt(TRADING_DAYS)   # de-annualise to per-period
    g = _skew(daily_rets)
    k = _kurtosis(daily_rets)
    # Variance of the Sharpe ESTIMATOR (Mertens/López de Prado), used both as the
    # test denominator and to scale the expected-max benchmark.
    v_sr = (1 - g * sr + (k - 1) / 4.0 * sr ** 2) / (n - 1)
    if v_sr <= 0:
        return None
    sd_sr = math.sqrt(v_sr)
    if n_trials > 1:
        # Expected maximum Sharpe under the null across N trials (order statistic),
        # scaled into per-period Sharpe units by sd_sr.
        e_max = sd_sr * ((1 - 0.5772) * _inv_norm(1 - 1.0 / n_trials)
                         + 0.5772 * _inv_norm(1 - 1.0 / (n_trials * math.e)))
    else:
        e_max = 0.0
    return round(_norm_cdf((sr - e_max) / sd_sr), 3)


def _max_drawdown_pct(equities: list[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for e in equities:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, (peak - e) / peak * 100)
    return round(mdd, 2)


def _cagr_pct(equities: list[float], n_days: int) -> float | None:
    if len(equities) < 2 or equities[0] <= 0 or n_days < 30:
        return None
    years = n_days / 365.25
    growth = equities[-1] / equities[0]
    if years <= 0 or growth <= 0:
        return None
    return round((growth ** (1 / years) - 1) * 100, 2)


def _span_days(curve: list[dict]) -> int:
    dates = [p.get("t") for p in curve if p.get("t")]
    if len(dates) < 2:
        return 0
    try:
        a = date.fromisoformat(str(dates[0])[:10])
        b = date.fromisoformat(str(dates[-1])[:10])
        return max((b - a).days, 0)
    except Exception:
        return 0


def _by_year(closed: list[dict]) -> list[dict]:
    """Per-calendar-year P&L and win rate — a strategy that only worked one year
    is a red flag the aggregate hides."""
    years: dict[str, dict] = {}
    for t in closed:
        y = str(t.get("exit_time") or "")[:4]
        if not y.isdigit():
            continue
        d = years.setdefault(y, {"trades": 0, "wins": 0, "pnl": 0.0})
        d["trades"] += 1
        d["wins"] += 1 if t["pnl"] > 0 else 0
        d["pnl"] += t["pnl"]
    return [{"year": y, "trades": years[y]["trades"],
             "win_rate": round(years[y]["wins"] / years[y]["trades"] * 100, 1),
             "pnl": round(years[y]["pnl"], 2)}
            for y in sorted(years)]


def compute_metrics(trades: list[dict], starting_capital: float,
                    daily_equity: list[dict] | None = None,
                    benchmark: dict | None = None,
                    n_trials: int = 1) -> dict:
    """`trades` items need: pnl, r_multiple, exit_time (iso str or datetime).

    `daily_equity` (optional): [{"t": "YYYY-MM-DD", "equity": float}, …] — a true
    daily mark-to-market curve. When given, CAGR/Sharpe/Sortino/max-drawdown come
    from it; otherwise from the per-trade curve (fallback for live analytics).
    `benchmark`: {"return_pct", "cagr_pct", "sharpe"} for a NIFTY B&H comparison.
    `n_trials`: configs tested, for the deflated Sharpe.
    """
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        out = _empty()
        out["benchmark"] = benchmark
        return out

    closed = sorted(closed, key=lambda t: str(t.get("exit_time") or ""))
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    rs = [t["r_multiple"] for t in closed if t.get("r_multiple") is not None]

    if daily_equity:
        curve = daily_equity
        equities = [p["equity"] for p in daily_equity if p.get("equity") is not None]
        rets = _daily_returns(daily_equity)
        n_days = _span_days(daily_equity)
    else:
        equity = starting_capital
        curve = [{"t": None, "equity": round(equity, 2)}]
        for t in closed:
            equity += t["pnl"]
            curve.append({"t": str(t.get("exit_time") or "")[:10] or None,
                          "equity": round(equity, 2)})
        equities = [p["equity"] for p in curve]
        rets = [t["pnl"] / starting_capital for t in closed]  # coarse per-trade proxy
        n_days = _span_days(curve)

    sharpe = _sharpe(rets)
    net = sum(t["pnl"] for t in closed)
    holding = [t["holding_days"] for t in closed if t.get("holding_days") is not None]

    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "expectancy_r": round(sum(rs) / len(rs), 2) if rs else None,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_pct": _max_drawdown_pct(equities) if equities else None,
        "total_return_pct": round((equities[-1] / equities[0] - 1) * 100, 2)
        if daily_equity and equities and equities[0] > 0
        else round(net / starting_capital * 100, 2),
        "net_pnl": round(net, 2),
        "avg_holding_days": round(sum(holding) / len(holding), 1) if holding else None,
        # --- risk-adjusted (Phase 1) ---
        "cagr_pct": _cagr_pct(equities, n_days) if equities else None,
        "sharpe": sharpe,
        "sortino": _sortino(rets),
        "deflated_sharpe": _deflated_sharpe(rets, sharpe, n_trials),
        "benchmark": benchmark,
        "by_year": _by_year(closed),
        "equity_curve": curve,
    }
