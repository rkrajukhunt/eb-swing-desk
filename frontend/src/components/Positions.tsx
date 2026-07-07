import { useEffect, useState } from "react";
import { api, fmt, fmtInt } from "../api";
import type { Position } from "../types";

const POLL_MS = 5000;

export default function Positions() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = async () => {
    try {
      const data = await api.get<{ positions: Position[] }>("/api/paper/positions");
      setPositions(data.positions);
      setErr(null);
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, []);

  const exit = async (id: number) => {
    setBusyId(id);
    try {
      await api.post(`/api/paper/trades/${id}/exit`);
      await load();
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusyId(null);
    }
  };

  const toggleTrail = async (p: Position) => {
    try {
      await api.patch(`/api/paper/trades/${p.id}`, { trailing: !p.trailing });
      await load();
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  const totalUpnl = positions.reduce((a, p) => a + (p.unrealized_pnl ?? 0), 0);

  return (
    <div className="panel">
      <div className="toolbar">
        <div>Open positions: <b>{positions.length}</b> · Unrealized P&L:{" "}
          <b className={totalUpnl >= 0 ? "pos" : "neg"}>₹{fmt(totalUpnl)}</b>
          <span className="muted"> (live, refreshes every {POLL_MS / 1000}s, net of costs)</span>
        </div>
      </div>
      {err && <div className="error-bar">{err}</div>}
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th className="left">Symbol</th><th className="left">Strategy</th><th>Qty</th>
              <th>Entry</th><th>LTP</th><th>P&L ₹</th><th>P&L %</th><th>R</th>
              <th>SL</th><th>→SL %</th><th>Target</th><th>→Target %</th><th>Trail</th><th></th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.id} style={{ cursor: "default" }}>
                <td className="left"><b>{p.symbol}</b></td>
                <td className="left"><span className="strat-badge">{p.strategy}</span></td>
                <td>{fmtInt(p.qty)}</td>
                <td>{fmt(p.entry_price)}</td>
                <td>{fmt(p.ltp)}</td>
                <td className={(p.unrealized_pnl ?? 0) >= 0 ? "pos" : "neg"}>{fmt(p.unrealized_pnl)}</td>
                <td className={(p.unrealized_pnl_pct ?? 0) >= 0 ? "pos" : "neg"}>{fmt(p.unrealized_pnl_pct)}%</td>
                <td>{fmt(p.r_multiple)}</td>
                <td className="neg">{fmt(p.stop_loss)}</td>
                <td>{fmt(p.dist_to_sl_pct, 1)}%</td>
                <td className="pos">{fmt(p.target)}</td>
                <td>{fmt(p.dist_to_target_pct, 1)}%</td>
                <td>
                  <input type="checkbox" checked={p.trailing} onChange={() => toggleTrail(p)} title="ATR trailing stop" />
                </td>
                <td>
                  <button className="small danger" disabled={busyId === p.id} onClick={() => exit(p.id)}>
                    {busyId === p.id ? "…" : "Exit"}
                  </button>
                </td>
              </tr>
            ))}
            {positions.length === 0 && (
              <tr><td colSpan={14} className="left muted">No open paper positions. Use “Paper Buy” on a signal.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
        Auto-exit engine runs server-side: LTP ≤ SL → “Stopped Out”, LTP ≥ target → “Target Hit”.
        Trailing stops ratchet SL to (highest close − ATR×mult).
      </div>
    </div>
  );
}
