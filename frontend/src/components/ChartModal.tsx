import { useEffect, useRef, useState } from "react";
import { createChart, IChartApi } from "lightweight-charts";
import { api } from "../api";
import type { ChartData } from "../types";

export default function ChartModal({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    api.get<ChartData>(`/api/symbols/${symbol}/chart`).then((data) => {
      if (disposed || !ref.current) return;
      const chart = createChart(ref.current, {
        height: 420,
        layout: { background: { color: "#161b24" }, textColor: "#dbe2ee" },
        grid: { vertLines: { color: "#222a38" }, horzLines: { color: "#222a38" } },
        rightPriceScale: { borderColor: "#2a3242" },
        timeScale: { borderColor: "#2a3242" },
      });
      chartRef.current = chart;
      const candles = chart.addCandlestickSeries({
        upColor: "#22c55e", downColor: "#ef4444", borderVisible: false,
        wickUpColor: "#22c55e", wickDownColor: "#ef4444",
      });
      candles.setData(data.candles as any);

      const emaColors: Record<string, string> = { ema20: "#3b82f6", ema50: "#f59e0b", ema200: "#a855f7" };
      for (const [name, points] of Object.entries(data.emas)) {
        const line = chart.addLineSeries({ color: emaColors[name] ?? "#888", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        line.setData(points.filter((p) => p.value != null) as any);
      }

      if (data.levels) {
        const mk = (price: number, color: string, title: string) =>
          candles.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title });
        mk(data.levels.entry, "#3b82f6", "entry");
        mk(data.levels.stop_loss, "#ef4444", "SL");
        mk(data.levels.target_2r, "#22c55e", "2R");
        mk(data.levels.target_3r, "#16a34a", "3R");
      }
      chart.timeScale().fitContent();
    }).catch((e) => setErr(String(e.message || e)));

    return () => {
      disposed = true;
      chartRef.current?.remove();
      chartRef.current = null;
    };
  }, [symbol]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <h3>{symbol} — daily with EMA 20/50/200 + signal levels</h3>
        {err && <div className="error-bar">{err}</div>}
        <div ref={ref} className="chart-box" />
        <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
          EMA20 <span style={{ color: "#3b82f6" }}>■</span> · EMA50 <span style={{ color: "#f59e0b" }}>■</span> · EMA200 <span style={{ color: "#a855f7" }}>■</span>
        </div>
        <div style={{ marginTop: 12 }}><button className="secondary" onClick={onClose}>Close</button></div>
      </div>
    </div>
  );
}
