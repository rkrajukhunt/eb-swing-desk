import { useEffect, useState } from "react";
import { api, fmt, fmtInt } from "../api";

interface OptLeg {
  side: string; opt_type: string; strike: number; price: number;
  delta: number | null; iv_pct: number | null; entry_price?: number; ltp?: number | null;
}
interface OptSignal {
  id: number; created_at: string; underlying: string; expiry: string; dte: number;
  spot: number; regime: string; strategy: string; strategy_label: string;
  entry_ok: boolean; entry_block_reason: string;
  atm_iv_pct: number | null; expected_move: number | null;
  legs: OptLeg[]; credit: number | null; max_profit: number | null; max_loss: number | null;
  margin_per_lot: number | null; roc_pct: number | null; pop_pct: number | null;
  breakevens: number[]; lots_suggested: number; target_met: boolean; reasons: string[];
  chain_snapshot: { strike: number; ce: number | null; pe: number | null }[];
}
interface OptPosition {
  id: number; strategy: string; expiry: string; lots: number; lot_size: number;
  legs: OptLeg[]; net_credit: number; max_loss: number; margin_est: number;
  breakevens: number[]; short_strikes: Record<string, number>;
  entry_time: string; spot_entry: number; spot: number | null;
  cost_to_close: number | null; unrealized_pnl: number | null;
  return_on_margin_pct: number | null; profit_target_pnl: number;
}
interface OptClosed {
  id: number; strategy: string; strategy_label: string; expiry: string; lots: number;
  legs: OptLeg[]; net_credit: number; margin_est: number; exit_debit: number | null;
  exit_reason: string | null; entry_time: string; exit_time: string | null;
  pnl: number | null; costs: number; return_on_margin_pct: number | null;
  spot_entry: number; spot_exit: number | null;
}
interface WeeklyPerf {
  weekly_target_pct: number;
  weeks: { week: string; trades: number; pnl: number; margin: number; wins: number; return_on_margin_pct: number | null; target_met: boolean }[];
  overall: { trades: number; win_rate: number | null; net_pnl: number; avg_return_on_margin_pct: number | null };
}

const POLL_MS = 5000;

export default function Options() {
  const [signal, setSignal] = useState<OptSignal | null>(null);
  const [positions, setPositions] = useState<OptPosition[]>([]);
  const [closed, setClosed] = useState<OptClosed[]>([]);
  const [perf, setPerf] = useState<WeeklyPerf | null>(null);
  const [strategy, setStrategy] = useState("auto");
  const [lots, setLots] = useState(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showChain, setShowChain] = useState(false);

  const loadAll = async () => {
    try {
      const [s, p, c, w] = await Promise.all([
        api.get<{ signal: OptSignal | null }>("/api/options/signal/latest"),
        api.get<{ positions: OptPosition[] }>("/api/options/paper/positions"),
        api.get<{ trades: OptClosed[] }>("/api/options/paper/trades?status=closed"),
        api.get<WeeklyPerf>("/api/options/paper/performance"),
      ]);
      setSignal(s.signal);
      if (s.signal?.lots_suggested) setLots((l) => (l === 1 ? s.signal!.lots_suggested : l));
      setPositions(p.positions);
      setClosed(c.trades);
      setPerf(w);
    } catch (e: any) { setErr(String(e.message || e)); }
  };

  useEffect(() => {
    loadAll();
    const id = setInterval(async () => {
      try {
        const p = await api.get<{ positions: OptPosition[] }>("/api/options/paper/positions");
        setPositions(p.positions);
      } catch { /* transient */ }
    }, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const generate = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await api.post<{ signal: OptSignal }>("/api/options/signal/generate", { strategy });
      setSignal(r.signal);
      if (r.signal.lots_suggested) setLots(r.signal.lots_suggested);
    } catch (e: any) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  };

  const paperSell = async () => {
    if (!signal) return;
    setBusy(true); setErr(null);
    try {
      await api.post("/api/options/paper/trades", { signal_id: signal.id, lots });
      await loadAll();
    } catch (e: any) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  };

  const exit = async (id: number) => {
    try { await api.post(`/api/options/paper/trades/${id}/exit`); await loadAll(); }
    catch (e: any) { setErr(String(e.message || e)); }
  };

  const legRow = (l: OptLeg, i: number) => (
    <tr key={i} style={{ cursor: "default" }}>
      <td className={`left ${l.side === "sell" ? "neg" : "pos"}`}><b>{l.side.toUpperCase()}</b></td>
      <td className="left">{l.strike} {l.opt_type}</td>
      <td>{fmt(l.entry_price ?? l.price)}</td>
      <td>{l.ltp !== undefined ? fmt(l.ltp) : "—"}</td>
      <td>{l.delta != null ? fmt(l.delta, 3) : "—"}</td>
      <td>{l.iv_pct != null ? `${fmt(l.iv_pct, 1)}%` : "—"}</td>
    </tr>
  );

  return (
    <div className="panel">
      {err && <div className="error-bar" onClick={() => setErr(null)}>{err} (click to dismiss)</div>}

      {/* ---- Signal generation ---- */}
      <div className="toolbar">
        <label className="field">Structure
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="auto">Auto (regime-driven)</option>
            <option value="iron_condor">Iron Condor</option>
            <option value="bull_put_spread">Bull Put Spread</option>
            <option value="bear_call_spread">Bear Call Spread</option>
          </select>
        </label>
        <button onClick={generate} disabled={busy}>{busy ? "Working…" : "Generate Option Signal"}</button>
        <div className="muted" style={{ fontSize: 12, maxWidth: 520 }}>
          Weekly NIFTY premium selling, <b>defined-risk only</b> — every short leg is hedged by a bought leg.
          Strikes placed beyond the market-implied expected move, tightened until the weekly ROC target is met
          (bounded by the short-delta cap).
        </div>
      </div>

      {signal && !signal.entry_ok && (
        <div className="error-bar" style={{ borderColor: "var(--amber)", color: "var(--amber)", background: "rgba(245,158,11,.1)" }}>
          Entry blocked: {signal.entry_block_reason}
          <span className="muted"> · spot {fmt(signal.spot)} · {signal.expiry ? `expiry ${signal.expiry} (DTE ${fmt(signal.dte, 1)})` : ""}</span>
        </div>
      )}

      {signal && signal.entry_ok && (
        <>
          <h4 className="section">
            Recommendation — {signal.strategy_label} · expiry {signal.expiry} (DTE {fmt(signal.dte, 1)}) · regime {signal.regime}
          </h4>
          <div className="cards">
            <div className="card"><div className="k">Spot</div><div className="v">{fmt(signal.spot)}</div></div>
            <div className="card"><div className="k">ATM IV</div><div className="v">{fmt(signal.atm_iv_pct, 1)}%</div></div>
            <div className="card"><div className="k">Expected move</div><div className="v">±{fmt(signal.expected_move, 0)}</div></div>
            <div className="card"><div className="k">Net credit</div><div className="v pos">{fmt(signal.credit, 1)} pts</div></div>
            <div className="card"><div className="k">Max loss / lot</div><div className="v neg">₹{fmtInt(signal.margin_per_lot)}</div></div>
            <div className="card"><div className="k">ROC (week)</div>
              <div className={`v ${signal.target_met ? "pos" : ""}`}>{fmt(signal.roc_pct, 2)}%{signal.target_met ? " ✓" : " (below target)"}</div></div>
            <div className="card"><div className="k">POP</div><div className="v">{fmt(signal.pop_pct, 1)}%</div></div>
            <div className="card"><div className="k">Breakevens</div><div className="v" style={{ fontSize: 15 }}>{signal.breakevens.map((b) => fmt(b, 0)).join(" / ")}</div></div>
          </div>

          <div className="subgrid">
            <div>
              <table>
                <thead><tr><th className="left">Side</th><th className="left">Contract</th><th>Price</th><th>LTP</th><th>Delta</th><th>IV</th></tr></thead>
                <tbody>{signal.legs.map(legRow)}</tbody>
              </table>
              <div className="toolbar" style={{ marginTop: 12 }}>
                <label className="field">Lots (×{75})
                  <input type="number" min={1} value={lots} onChange={(e) => setLots(parseInt(e.target.value || "1"))} />
                </label>
                <button onClick={paperSell} disabled={busy || lots <= 0}>Paper Sell (live quotes)</button>
                <div className="muted" style={{ fontSize: 12 }}>
                  Suggested {signal.lots_suggested} lot(s) · margin ≈ ₹{fmtInt((signal.margin_per_lot ?? 0) * lots)} ·
                  max profit ≈ ₹{fmtInt((signal.max_profit ?? 0) * 75 * lots)}
                </div>
              </div>
            </div>
            <div>
              <div className="tt-body" style={{ display: "block", position: "static", width: "auto" }}>
                <b>Why (formula audit)</b>
                <ul>{signal.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
                <button className="secondary small" style={{ marginTop: 8 }} onClick={() => setShowChain(!showChain)}>
                  {showChain ? "Hide" : "Show"} chain near ATM
                </button>
                {showChain && (
                  <table style={{ marginTop: 8 }}>
                    <thead><tr><th>Call</th><th>Strike</th><th>Put</th></tr></thead>
                    <tbody>
                      {signal.chain_snapshot.map((r) => (
                        <tr key={r.strike} style={{ cursor: "default" }}>
                          <td>{fmt(r.ce)}</td>
                          <td style={{ textAlign: "center", fontWeight: Math.abs(r.strike - signal.spot) < 25 ? 700 : 400 }}>{r.strike}</td>
                          <td>{fmt(r.pe)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* ---- Open option positions ---- */}
      <h4 className="section">Open option positions (live MTM, {POLL_MS / 1000}s poll)</h4>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th className="left">Strategy</th><th className="left">Expiry</th><th>Lots</th>
              <th className="left">Legs</th><th>Credit</th><th>Cost to close</th>
              <th>P&L ₹</th><th>RoM %</th><th>Spot</th><th className="left">Shorts</th><th></th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.id} style={{ cursor: "default" }}>
                <td className="left"><span className="strat-badge">{p.strategy}</span></td>
                <td className="left">{p.expiry}</td>
                <td>{p.lots}</td>
                <td className="left" style={{ fontSize: 12 }}>
                  {p.legs.map((l, i) => (
                    <div key={i}>
                      <span className={l.side === "sell" ? "neg" : "pos"}>{l.side === "sell" ? "S" : "B"}</span>{" "}
                      {l.strike}{l.opt_type} @ {fmt(l.entry_price)} → {fmt(l.ltp)}
                    </div>
                  ))}
                </td>
                <td className="pos">{fmt(p.net_credit, 1)}</td>
                <td>{fmt(p.cost_to_close, 1)}</td>
                <td className={(p.unrealized_pnl ?? 0) >= 0 ? "pos" : "neg"}>{fmt(p.unrealized_pnl)}</td>
                <td className={(p.return_on_margin_pct ?? 0) >= 0 ? "pos" : "neg"}>{fmt(p.return_on_margin_pct)}%</td>
                <td>{fmt(p.spot, 0)}</td>
                <td className="left muted" style={{ fontSize: 12 }}>
                  {Object.entries(p.short_strikes).map(([t, k]) => `${k}${t}`).join(" · ")}
                </td>
                <td><button className="small danger" onClick={() => exit(p.id)}>Exit</button></td>
              </tr>
            ))}
            {positions.length === 0 && <tr><td colSpan={11} className="left muted">No open option positions.</td></tr>}
          </tbody>
        </table>
      </div>

      {/* ---- Weekly performance ---- */}
      {perf && (
        <>
          <h4 className="section">Weekly performance vs {perf.weekly_target_pct}% target (return on margin)</h4>
          <div className="cards">
            <div className="card"><div className="k">Closed trades</div><div className="v">{perf.overall.trades}</div></div>
            <div className="card"><div className="k">Win rate</div><div className="v">{fmt(perf.overall.win_rate, 1)}%</div></div>
            <div className="card"><div className="k">Net P&L</div>
              <div className={`v ${perf.overall.net_pnl >= 0 ? "pos" : "neg"}`}>₹{fmt(perf.overall.net_pnl)}</div></div>
            <div className="card"><div className="k">Avg RoM</div><div className="v">{fmt(perf.overall.avg_return_on_margin_pct)}%</div></div>
          </div>
          {perf.weeks.length > 0 && (
            <table>
              <thead><tr><th className="left">Week</th><th>Trades</th><th>Wins</th><th>Margin used</th><th>P&L ₹</th><th>Return %</th><th className="left">Target</th></tr></thead>
              <tbody>
                {perf.weeks.map((w) => (
                  <tr key={w.week} style={{ cursor: "default" }}>
                    <td className="left">{w.week}</td>
                    <td>{w.trades}</td><td>{w.wins}</td>
                    <td>{fmtInt(w.margin)}</td>
                    <td className={w.pnl >= 0 ? "pos" : "neg"}>{fmt(w.pnl)}</td>
                    <td className={(w.return_on_margin_pct ?? 0) >= 0 ? "pos" : "neg"}>{fmt(w.return_on_margin_pct)}%</td>
                    <td className={`left ${w.target_met ? "pos" : "muted"}`}>{w.target_met ? "✓ met" : "below"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {/* ---- Closed trades journal ---- */}
      <h4 className="section">Closed option trades</h4>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th className="left">Strategy</th><th className="left">Expiry</th><th>Lots</th>
              <th>Credit</th><th>Exit debit</th><th className="left">Reason</th>
              <th>P&L ₹</th><th>Costs</th><th>RoM %</th><th className="left">Exited</th>
            </tr>
          </thead>
          <tbody>
            {closed.map((t) => (
              <tr key={t.id} style={{ cursor: "default" }}>
                <td className="left"><span className="strat-badge">{t.strategy}</span></td>
                <td className="left">{t.expiry}</td>
                <td>{t.lots}</td>
                <td className="pos">{fmt(t.net_credit, 1)}</td>
                <td>{fmt(t.exit_debit, 1)}</td>
                <td className="left">{t.exit_reason}</td>
                <td className={(t.pnl ?? 0) >= 0 ? "pos" : "neg"}>{fmt(t.pnl)}</td>
                <td className="muted">{fmt(t.costs)}</td>
                <td className={(t.return_on_margin_pct ?? 0) >= 0 ? "pos" : "neg"}>{fmt(t.return_on_margin_pct)}</td>
                <td className="left muted">{t.exit_time ? new Date(t.exit_time + "Z").toLocaleString() : "—"}</td>
              </tr>
            ))}
            {closed.length === 0 && <tr><td colSpan={10} className="left muted">No closed option trades yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
        Auto-exit engine (server-side): profit target ({/* from settings */}% of max), stop at ×credit loss,
        short-strike breach, and expiry settlement at intrinsic value. A 2–3% weekly return-on-margin target implies
        meaningful tail risk — max loss per structure is capped by the hedge legs, but weeks where the index moves
        beyond the expected move WILL show losses larger than several weeks of gains. Position sizing matters.
      </div>
    </div>
  );
}
