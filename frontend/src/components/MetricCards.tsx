import { useEffect, useRef } from "react";
import { createChart, IChartApi } from "lightweight-charts";
import { fmt } from "../api";
import type { Metrics } from "../types";

export function MetricCards({ m }: { m: Metrics }) {
  const card = (k: string, v: string, cls = "") => (
    <div className="card" key={k}><div className="k">{k}</div><div className={`v ${cls}`}>{v}</div></div>
  );
  const sgn = (x: number | null | undefined) => ((x ?? 0) >= 0 ? "pos" : "neg");
  const bmRet = m.benchmark?.return_pct;
  const alpha = m.total_return_pct != null && bmRet != null ? m.total_return_pct - bmRet : null;
  return (
    <>
      <div className="cards">
        {card("Trades", String(m.trades))}
        {card("Win rate", m.win_rate != null ? `${m.win_rate}%` : "—")}
        {card("Total return", m.total_return_pct != null ? `${m.total_return_pct}%` : "—", sgn(m.total_return_pct))}
        {card("CAGR", m.cagr_pct != null ? `${m.cagr_pct}%` : "—", sgn(m.cagr_pct))}
        {card("Sharpe", m.sharpe != null ? String(m.sharpe) : "—", sgn(m.sharpe))}
        {card("Sortino", m.sortino != null ? String(m.sortino) : "—", sgn(m.sortino))}
        {card("Max drawdown", m.max_drawdown_pct != null ? `${m.max_drawdown_pct}%` : "—", "neg")}
        {card("Profit factor", m.profit_factor != null ? String(m.profit_factor) : "—", (m.profit_factor ?? 0) >= 1 ? "pos" : "neg")}
        {card("Expectancy", m.expectancy_r != null ? `${m.expectancy_r} R` : "—", sgn(m.expectancy_r))}
        {card("Avg holding", m.avg_holding_days != null ? `${m.avg_holding_days} d` : "—")}
        {card("Net P&L", `₹${fmt(m.net_pnl)}`, sgn(m.net_pnl))}
        {m.deflated_sharpe != null &&
          card("Deflated Sharpe", `${(m.deflated_sharpe * 100).toFixed(0)}%`,
               m.deflated_sharpe >= 0.95 ? "pos" : "neg")}
      </div>

      {(bmRet != null || alpha != null) && (
        <div className="muted" style={{ margin: "8px 0 4px", fontSize: 13 }}>
          {bmRet != null && <>NIFTY buy-and-hold, same window: <b>{bmRet}%</b>
            {m.benchmark?.sharpe != null && <> (Sharpe {m.benchmark.sharpe})</>}. </>}
          {alpha != null && (
            <span className={alpha >= 0 ? "pos" : "neg"}>
              Strategy {alpha >= 0 ? "beat" : "lagged"} the index by {Math.abs(alpha).toFixed(1)} pts.
            </span>
          )}
          {m.deflated_sharpe != null && m.deflated_sharpe < 0.95 &&
            <span className="neg"> &nbsp;Deflated Sharpe &lt; 95% — not statistically convincing yet.</span>}
        </div>
      )}

      {m.by_year && m.by_year.length > 0 && (
        <table className="tbl" style={{ marginTop: 6, fontSize: 13 }}>
          <thead><tr><th>Year</th><th>Trades</th><th>Win %</th><th>P&amp;L ₹</th></tr></thead>
          <tbody>
            {m.by_year.map((y) => (
              <tr key={y.year}>
                <td>{y.year}</td><td>{y.trades}</td><td>{y.win_rate}%</td>
                <td className={y.pnl >= 0 ? "pos" : "neg"}>₹{fmt(y.pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

export function EquityCurve({ curve, height = 260 }: { curve: Metrics["equity_curve"]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!ref.current || curve.length < 2) return;
    const chart = createChart(ref.current, {
      height,
      layout: { background: { color: "#161b24" }, textColor: "#dbe2ee" },
      grid: { vertLines: { color: "#222a38" }, horzLines: { color: "#222a38" } },
      rightPriceScale: { borderColor: "#2a3242" },
      timeScale: { borderColor: "#2a3242" },
    });
    chartRef.current = chart;
    const line = chart.addAreaSeries({
      lineColor: "#6366f1", topColor: "rgba(99,102,241,0.35)", bottomColor: "rgba(99,102,241,0.02)",
      lineWidth: 2,
    });
    // equity_curve entries may lack dates (live trades on same day) — index them
    let lastDate = "";
    let bump = 0;
    const data = curve.map((p, i) => {
      let t = p.t || lastDate;
      if (!t) t = "2000-01-01";
      if (t === lastDate) bump += 1; else bump = 0;
      lastDate = t;
      // lightweight-charts needs strictly increasing unique times; collapse dupes
      return { time: t, value: p.equity, _i: i };
    });
    const dedup = new Map<string, number>();
    for (const d of data) dedup.set(d.time as string, d.value);
    line.setData(Array.from(dedup, ([time, value]) => ({ time, value })) as any);
    chart.timeScale().fitContent();
    return () => { chartRef.current?.remove(); chartRef.current = null; };
  }, [curve, height]);

  if (curve.length < 2) return <div className="muted">Not enough closed trades for an equity curve.</div>;
  return <div ref={ref} />;
}
