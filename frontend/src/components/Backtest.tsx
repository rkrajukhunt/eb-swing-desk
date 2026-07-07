import { useState } from "react";
import { api, fmt } from "../api";
import type { BacktestResult } from "../types";
import { EquityCurve, MetricCards } from "./MetricCards";

export default function Backtest() {
  const [strategy, setStrategy] = useState("all");
  const [years, setYears] = useState(3);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setResult(await api.post<BacktestResult>("/api/backtest/run", { strategy, years }));
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <div className="toolbar">
        <label className="field">Strategy
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="all">All strategies</option>
            <option value="trend_pullback">Trend Pullback</option>
            <option value="breakout">Breakout</option>
            <option value="mean_reversion">Mean Reversion</option>
          </select>
        </label>
        <label className="field">Years
          <input type="number" min={1} max={6} value={years} onChange={(e) => setYears(parseInt(e.target.value || "3"))} />
        </label>
        <button onClick={run} disabled={busy}>{busy ? "Backtesting… (bar-by-bar over the universe)" : "Run Backtest"}</button>
        <div className="muted" style={{ fontSize: 12, maxWidth: 460 }}>
          Uses the <b>exact same</b> strategy, signal-math and cost code path as live scanning —
          entry at next-day open, SL-first fills, brokerage + slippage included.
        </div>
      </div>

      {err && <div className="error-bar">{err}</div>}

      {result && (
        <>
          <h4 className="section">
            Result — {result.strategy} · {result.years}y · {result.symbols_tested} symbols
          </h4>
          <MetricCards m={result.overall} />
          <EquityCurve curve={result.overall.equity_curve} />

          <h4 className="section">By strategy</h4>
          <div className="subgrid">
            {Object.entries(result.by_strategy).map(([name, m]) => (
              <div key={name} className="panel" style={{ marginBottom: 0 }}>
                <b>{name}</b>
                <MetricCards m={m} />
              </div>
            ))}
          </div>

          <h4 className="section">Trades (last {result.trades.length})</h4>
          <div style={{ overflowX: "auto", maxHeight: 380, overflowY: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th className="left">Symbol</th><th className="left">Strategy</th>
                  <th className="left">Entry date</th><th className="left">Exit date</th>
                  <th>Entry</th><th>Exit</th><th className="left">Reason</th><th>P&L ₹</th><th>R</th><th>Days</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.slice().reverse().map((t, i) => (
                  <tr key={i} style={{ cursor: "default" }}>
                    <td className="left"><b>{t.symbol}</b></td>
                    <td className="left"><span className="strat-badge">{t.strategy}</span></td>
                    <td className="left muted">{t.entry_time}</td>
                    <td className="left muted">{t.exit_time}</td>
                    <td>{fmt(t.entry)}</td>
                    <td>{fmt(t.exit)}</td>
                    <td className="left">{t.reason}</td>
                    <td className={t.pnl >= 0 ? "pos" : "neg"}>{fmt(t.pnl)}</td>
                    <td className={t.r_multiple >= 0 ? "pos" : "neg"}>{fmt(t.r_multiple)}</td>
                    <td>{t.holding_days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
