import { useMemo, useState } from "react";
import { fmt, fmtInt } from "../api";
import type { ScanLatest, SignalRow } from "../types";
import ChartModal from "./ChartModal";
import PaperBuyModal from "./PaperBuyModal";

type SortKey = "score" | "symbol" | "entry" | "rr" | "rsi" | "rs" | "vol" | "llm";

export default function Signals({
  scan, scanning, onRunScan, onReload,
}: {
  scan: ScanLatest | null;
  scanning: boolean;
  onRunScan: (strategy: string) => void;
  onReload: () => void;
}) {
  const [strategy, setStrategy] = useState("all");
  const [filter, setFilter] = useState("");
  const [minScore, setMinScore] = useState(50);   // hide low-conviction setups
  const [sortKey, setSortKey] = useState<SortKey>("llm");
  const [sortAsc, setSortAsc] = useState(false);
  const [buySignal, setBuySignal] = useState<SignalRow | null>(null);
  const [chartSymbol, setChartSymbol] = useState<string | null>(null);

  const rows = useMemo(() => {
    let out = scan?.signals ?? [];
    out = out.filter((s) => s.composite_score >= minScore);
    if (filter) {
      const f = filter.toUpperCase();
      out = out.filter((s) => s.symbol.includes(f) || s.strategy_label.toUpperCase().includes(f));
    }
    const key = (s: SignalRow): number | string => {
      switch (sortKey) {
        case "symbol": return s.symbol;
        case "entry": return s.entry;
        case "rr": return s.risk_reward;
        case "rsi": return s.indicators?.rsi14 ?? 0;
        case "rs": return s.indicators?.rel_strength_55d ?? 0;
        case "vol": return s.indicators?.vol_vs_avg ?? 0;
        case "llm": return s.llm_rank ?? 9999;
        default: return s.composite_score;
      }
    };
    out = [...out].sort((a, b) => {
      const ka = key(a), kb = key(b);
      const cmp = typeof ka === "string" ? ka.localeCompare(kb as string) : (ka as number) - (kb as number);
      return sortAsc ? cmp : -cmp;
    });
    if (sortKey === "llm" && !sortAsc) {
      // llm rank: lower is better — flip default direction
      out.reverse();
    }
    return out;
  }, [scan, filter, minScore, sortKey, sortAsc]);

  const th = (label: string, key: SortKey, cls = "") => (
    <th className={cls} onClick={() => (sortKey === key ? setSortAsc(!sortAsc) : (setSortKey(key), setSortAsc(false)))}>
      {label}{sortKey === key ? (sortAsc ? " ▲" : " ▼") : ""}
    </th>
  );

  const scorePill = (s: SignalRow) => {
    const cls = s.composite_score >= 70 ? "score-hi" : s.composite_score >= 50 ? "score-mid" : "score-lo";
    return (
      <span className={`tt`}>
        <span className={`score-pill ${cls}`}>{s.composite_score}</span>
        <div className="tt-body">
          <b>Sub-scores</b> (weighted blend)
          <ul>
            {Object.entries(s.sub_scores).map(([k, v]) => <li key={k}>{k}: {v}</li>)}
          </ul>
          {s.penalties.length > 0 && (<><b>Penalties</b><ul>{s.penalties.map((p, i) => <li key={i}>{p}</li>)}</ul></>)}
        </div>
      </span>
    );
  };

  const whyTip = (s: SignalRow) => (
    <span className="tt">
      <span className="strat-badge">{s.strategy_label}</span>
      <div className="tt-body">
        <b>Why this signal (formula audit)</b>
        <ul>{s.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
      </div>
    </span>
  );

  return (
    <div className="panel">
      <div className="toolbar">
        <label className="field">
          Strategy preset
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="all">All strategies</option>
            <option value="trend_pullback">Trend Pullback</option>
            <option value="breakout">Breakout</option>
            <option value="mean_reversion">Mean Reversion</option>
          </select>
        </label>
        <button onClick={() => onRunScan(strategy)} disabled={scanning}>
          {scanning ? "Scanning… (fetching data + indicators)" : "Run Scan"}
        </button>
        <label className="field">
          Filter
          <input type="text" placeholder="symbol / strategy" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </label>
        <label className="field">
          Min score
          <input type="number" min={0} max={100} step={5} style={{ width: 72 }}
                 value={minScore} onChange={(e) => setMinScore(Number(e.target.value) || 0)} />
        </label>
        {scan?.run && (
          <div className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>
            Run #{scan.run.id} · {new Date(scan.run.started_at + "Z").toLocaleString()} · universe {scan.run.universe} ·
            scanned {scan.run.scanned_count} · signals {scan.run.signal_count} ·
            {scan.run.llm_used ? " LLM-ranked ✓" : " deterministic order"}
          </div>
        )}
      </div>

      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              {th("#", "llm", "left")}
              {th("Symbol", "symbol", "left")}
              {th("Score", "score")}
              <th className="left">Strategy</th>
              {th("Entry", "entry")}
              <th>SL</th>
              <th>Target 2R</th>
              <th>Target 3R</th>
              {th("R:R", "rr")}
              <th>Risk %</th>
              {th("RSI", "rsi")}
              <th>Trend</th>
              {th("RS 55d", "rs")}
              {th("Vol×", "vol")}
              <th className="left">LLM rationale</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} onClick={() => setChartSymbol(s.symbol)}>
                <td className="left muted">{s.llm_rank ?? "—"}</td>
                <td className="left"><b>{s.symbol}</b></td>
                <td>{scorePill(s)}</td>
                <td className="left">{whyTip(s)}</td>
                <td>{fmt(s.entry)}</td>
                <td className="neg">{fmt(s.stop_loss)}</td>
                <td className="pos">{fmt(s.target_2r)}</td>
                <td className="pos">{fmt(s.target_3r)}</td>
                <td>{fmt(s.risk_reward, 1)}</td>
                <td>{fmt(s.pct_risk, 1)}%</td>
                <td>{fmt(s.indicators?.rsi14, 1)}</td>
                <td className={s.indicators?.weekly_trend === "up" ? "pos" : "neg"}>
                  {s.indicators?.weekly_trend === "up" ? "W↑" : "W↓"}
                </td>
                <td className={(s.indicators?.rel_strength_55d ?? 0) >= 0 ? "pos" : "neg"}>
                  {fmt(s.indicators?.rel_strength_55d, 1)}
                </td>
                <td className={(s.indicators?.vol_vs_avg ?? 0) >= 1.5 ? "pos" : ""}>{fmt(s.indicators?.vol_vs_avg, 2)}</td>
                <td className="left" style={{ whiteSpace: "normal", maxWidth: 340, fontSize: 12 }}>
                  {s.llm_conviction && <span className={`conv-${s.llm_conviction}`}>[{s.llm_conviction}] </span>}
                  {s.llm_rationale ?? <span className="muted">—</span>}
                  {s.llm_conflicts?.length > 0 && (
                    <div className="neg" style={{ fontSize: 11 }}>⚠ {s.llm_conflicts.join("; ")}</div>
                  )}
                </td>
                <td>
                  <button className="small" onClick={(e) => { e.stopPropagation(); setBuySignal(s); }}>
                    Paper Buy
                  </button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={16} className="left muted">
                {!scan?.run
                  ? "No scan yet. Hit Run Scan."
                  : (scan.signals.length > 0
                      ? `All ${scan.signals.length} signal(s) are below the min-score filter (${minScore}) or don't match the text filter — lower "Min score" to see them.`
                      : "No signals in the latest scan — regime gate, liquidity guards or strategy filters rejected everything.")}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {buySignal && (
        <PaperBuyModal signal={buySignal} onClose={() => setBuySignal(null)} onDone={() => { setBuySignal(null); onReload(); }} />
      )}
      {chartSymbol && <ChartModal symbol={chartSymbol} onClose={() => setChartSymbol(null)} />}
    </div>
  );
}
