import { useMemo, useState } from "react";
import { fmt, fmtInt } from "../api";
import type { ScanLatest, SignalRow } from "../types";
import ChartModal from "./ChartModal";
import PaperBuyModal from "./PaperBuyModal";

type SortKey = "score" | "symbol" | "entry" | "rr" | "rsi" | "rs" | "vol" | "llm";

// Plain-English buy verdict so a non-expert can act without decoding the numbers.
// Encodes the exact selection rules: weekly uptrend + beating the index + a
// worthwhile reward:risk + a sane stop, and no dip-buying in a weak market.
type Grade = { tier: "strong" | "ok" | "avoid"; rank: number; label: string; tip: string };

function grade(s: SignalRow, regime: string): Grade {
  const wUp = s.indicators?.weekly_trend === "up";
  const rs = s.indicators?.rel_strength_55d ?? 0;
  const rr = s.risk_reward ?? 0;
  const risk = s.pct_risk ?? 99;
  const knife = s.strategy === "mean_reversion" && regime !== "bullish";

  const bad: string[] = [];
  if (!wUp) bad.push("weekly trend is DOWN");
  if (rs < 0) bad.push("weaker than the index");
  if (knife) bad.push("buying a dip in a weak market (risky)");

  if (wUp && rs >= 5 && rr >= 2.5 && risk <= 2.5 && !knife) {
    return {
      tier: "strong", rank: 0, label: "✓ Buy",
      tip: `Strong uptrend, beating the market by ${rs.toFixed(1)}%, reward:risk ${rr.toFixed(1)}, tight stop (${risk.toFixed(1)}% risk). Passes every quality filter.`,
    };
  }
  if (bad.length > 0) {
    return { tier: "avoid", rank: 2, label: "✗ Skip", tip: `Fails a key check: ${bad.join("; ")}.` };
  }
  return {
    tier: "ok", rank: 1, label: "~ Consider",
    tip: "Decent, but not top-tier — one or more of relative strength, reward:risk or risk-size is only borderline.",
  };
}

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
  const [onlyPicks, setOnlyPicks] = useState(false);  // show only ✓ Buy rows
  const [sortKey, setSortKey] = useState<SortKey>("llm");
  const [sortAsc, setSortAsc] = useState(false);
  const [buySignal, setBuySignal] = useState<SignalRow | null>(null);
  const [chartSymbol, setChartSymbol] = useState<string | null>(null);

  const regime = scan?.run?.market_regime ?? "unknown";

  const rows = useMemo(() => {
    let out = scan?.signals ?? [];
    out = out.filter((s) => s.composite_score >= minScore);
    if (onlyPicks) out = out.filter((s) => grade(s, regime).tier === "strong");
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
    // Always float the ✓ Buy rows to the top so the shortlist is what you see first.
    out = [...out].sort((a, b) => grade(a, regime).rank - grade(b, regime).rank);
    return out;
  }, [scan, filter, minScore, onlyPicks, regime, sortKey, sortAsc]);

  const strongCount = useMemo(
    () => (scan?.signals ?? []).filter((s) => grade(s, regime).tier === "strong").length,
    [scan, regime],
  );

  const th = (label: string, key: SortKey, cls = "") => (
    <th className={cls} onClick={() => (sortKey === key ? setSortAsc(!sortAsc) : (setSortKey(key), setSortAsc(false)))}>
      {label}{sortKey === key ? (sortAsc ? " ▲" : " ▼") : ""}
    </th>
  );

  const pickBadge = (s: SignalRow) => {
    const g = grade(s, regime);
    const cls = g.tier === "strong" ? "pos" : g.tier === "avoid" ? "neg" : "muted";
    return (
      <span className="tt">
        <b className={cls} style={{ whiteSpace: "nowrap" }}>{g.label}</b>
        <div className="tt-body">{g.tip}</div>
      </span>
    );
  };

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
        <label className="checkbox-row" style={{ alignSelf: "flex-end", marginBottom: 4 }}>
          <input type="checkbox" checked={onlyPicks} onChange={(e) => setOnlyPicks(e.target.checked)} />
          ⭐ Only show buys
        </label>
        {scan?.run && (
          <div className="muted" style={{ marginLeft: "auto", fontSize: 12 }}>
            Run #{scan.run.id} · {new Date(scan.run.started_at + "Z").toLocaleString()} · universe {scan.run.universe} ·
            scanned {scan.run.scanned_count} · signals {scan.run.signal_count} ·
            {scan.run.llm_used ? " LLM-ranked ✓" : " deterministic order"}
          </div>
        )}
      </div>

      {scan?.run && (
        <div className="muted" style={{ margin: "4px 2px 10px", fontSize: 13 }}>
          {strongCount > 0 ? (
            <>🎯 <b className="pos">{strongCount} strong {strongCount === 1 ? "pick" : "picks"}</b> (green <b className="pos">✓ Buy</b>) —
            these pass every quality check. Focus on those; tick <b>“Only show buys”</b> to hide the rest.
            Don’t buy all of them — pick the top 3–5 and use the suggested quantity.</>
          ) : (
            <>No <b>✓ Buy</b>-grade setups in this scan — the market regime is <b>{regime}</b>, so the safe move is to wait.
            The rows below are lower-conviction; skipping entirely is a valid choice.</>
          )}
        </div>
      )}

      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th className="left">Pick</th>
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
                <td className="left">{pickBadge(s)}</td>
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
              <tr><td colSpan={17} className="left muted">
                {!scan?.run
                  ? "No scan yet. Hit Run Scan."
                  : onlyPicks && strongCount === 0
                    ? `No ✓ Buy-grade setups in this scan (regime: ${regime}). Untick "Only show buys" to see lower-conviction rows, or wait for a better market.`
                    : (scan.signals.length > 0
                        ? `All ${scan.signals.length} signal(s) are hidden by your filters (min score ${minScore}${onlyPicks ? ", buys-only" : ""}, or the text filter) — relax them to see more.`
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
