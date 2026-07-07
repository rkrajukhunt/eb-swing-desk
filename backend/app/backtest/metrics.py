"""Performance metrics shared by the backtester AND live paper analytics —
one formula set, so the two are directly comparable."""
from __future__ import annotations


def compute_metrics(trades: list[dict], starting_capital: float) -> dict:
    """`trades` items need: pnl, r_multiple, exit_time (iso str or datetime).

    Returns win rate, expectancy (in R), profit factor, drawdown, equity curve…
    """
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        return {
            "trades": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
            "expectancy_r": None, "profit_factor": None, "avg_r": None,
            "max_drawdown_pct": None, "total_return_pct": None, "net_pnl": 0.0,
            "avg_holding_days": None, "equity_curve": [],
        }

    def _key(t):
        et = t.get("exit_time")
        return str(et) if et else ""

    closed = sorted(closed, key=_key)
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    rs = [t["r_multiple"] for t in closed if t.get("r_multiple") is not None]

    equity = starting_capital
    peak = equity
    max_dd = 0.0
    curve = [{"t": None, "equity": round(equity, 2)}]
    for t in closed:
        equity += t["pnl"]
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
        curve.append({"t": _key(t)[:10] or None, "equity": round(equity, 2)})

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
        "max_drawdown_pct": round(max_dd, 2),
        "total_return_pct": round(net / starting_capital * 100, 2),
        "net_pnl": round(net, 2),
        "avg_holding_days": round(sum(holding) / len(holding), 1) if holding else None,
        "equity_curve": curve,
    }
