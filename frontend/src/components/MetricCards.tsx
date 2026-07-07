import { useEffect, useRef } from "react";
import { createChart, IChartApi } from "lightweight-charts";
import { fmt } from "../api";
import type { Metrics } from "../types";

export function MetricCards({ m }: { m: Metrics }) {
  const card = (k: string, v: string, cls = "") => (
    <div className="card" key={k}><div className="k">{k}</div><div className={`v ${cls}`}>{v}</div></div>
  );
  return (
    <div className="cards">
      {card("Trades", String(m.trades))}
      {card("Win rate", m.win_rate != null ? `${m.win_rate}%` : "—")}
      {card("Avg win", m.avg_win != null ? `₹${fmt(m.avg_win)}` : "—", "pos")}
      {card("Avg loss", m.avg_loss != null ? `₹${fmt(m.avg_loss)}` : "—", "neg")}
      {card("Expectancy", m.expectancy_r != null ? `${m.expectancy_r} R` : "—", (m.expectancy_r ?? 0) >= 0 ? "pos" : "neg")}
      {card("Profit factor", m.profit_factor != null ? String(m.profit_factor) : "—")}
      {card("Max drawdown", m.max_drawdown_pct != null ? `${m.max_drawdown_pct}%` : "—", "neg")}
      {card("Total return", m.total_return_pct != null ? `${m.total_return_pct}%` : "—", (m.total_return_pct ?? 0) >= 0 ? "pos" : "neg")}
      {card("Net P&L", `₹${fmt(m.net_pnl)}`, m.net_pnl >= 0 ? "pos" : "neg")}
      {card("Avg holding", m.avg_holding_days != null ? `${m.avg_holding_days} d` : "—")}
    </div>
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
