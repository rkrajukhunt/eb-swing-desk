import { useEffect, useState } from "react";
import { api } from "../api";
import type { Metrics } from "../types";
import { EquityCurve, MetricCards } from "./MetricCards";

interface Perf {
  overall: Metrics;
  by_strategy: Record<string, Metrics>;
}

export default function Performance() {
  const [perf, setPerf] = useState<Perf | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.get<Perf>("/api/paper/performance").then(setPerf).catch((e) => setErr(String(e.message || e)));
  }, []);

  if (err) return <div className="panel"><div className="error-bar">{err}</div></div>;
  if (!perf) return <div className="panel muted">Loading…</div>;

  const strategies = Object.entries(perf.by_strategy);

  return (
    <div className="panel">
      <h4 className="section">Live paper-trading performance — overall</h4>
      <MetricCards m={perf.overall} />
      <EquityCurve curve={perf.overall.equity_curve} />

      <h4 className="section">Side-by-side by strategy preset (same formulas as the backtester)</h4>
      {strategies.length === 0 && <div className="muted">No closed trades yet — strategy comparison appears here.</div>}
      <div className="subgrid">
        {strategies.map(([name, m]) => (
          <div key={name} className="panel" style={{ marginBottom: 0 }}>
            <b>{name}</b>
            <MetricCards m={m} />
          </div>
        ))}
      </div>
    </div>
  );
}
