import { useEffect, useState } from "react";
import { api, fmt, fmtInt } from "../api";
import type { ClosedTrade } from "../types";

export default function ClosedTrades() {
  const [trades, setTrades] = useState<ClosedTrade[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ trades: ClosedTrade[] }>("/api/paper/trades?status=closed")
      .then((d) => setTrades(d.trades))
      .catch((e) => setErr(String(e.message || e)));
  }, []);

  const net = trades.reduce((a, t) => a + (t.pnl ?? 0), 0);

  return (
    <div className="panel">
      <div className="toolbar">
        <div>Journal — {trades.length} closed trades · Net P&L:{" "}
          <b className={net >= 0 ? "pos" : "neg"}>₹{fmt(net)}</b>
          <span className="muted"> (after brokerage + slippage)</span>
        </div>
      </div>
      {err && <div className="error-bar">{err}</div>}
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th className="left">Symbol</th><th className="left">Strategy</th><th>Qty</th>
              <th>Entry</th><th>Exit</th><th className="left">Reason</th>
              <th>P&L ₹</th><th>Costs</th><th>R</th><th>Days</th>
              <th className="left">Entered</th><th className="left">Exited</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} style={{ cursor: "default" }}>
                <td className="left"><b>{t.symbol}</b></td>
                <td className="left"><span className="strat-badge">{t.strategy}</span></td>
                <td>{fmtInt(t.qty)}</td>
                <td>{fmt(t.entry_price)}</td>
                <td>{fmt(t.exit_price)}</td>
                <td className="left">{t.exit_reason}</td>
                <td className={(t.pnl ?? 0) >= 0 ? "pos" : "neg"}>{fmt(t.pnl)}</td>
                <td className="muted">{fmt(t.costs)}</td>
                <td className={(t.r_multiple ?? 0) >= 0 ? "pos" : "neg"}>{fmt(t.r_multiple)}</td>
                <td>{t.holding_days ?? "—"}</td>
                <td className="left muted">{new Date(t.entry_time + "Z").toLocaleDateString()}</td>
                <td className="left muted">{t.exit_time ? new Date(t.exit_time + "Z").toLocaleDateString() : "—"}</td>
              </tr>
            ))}
            {trades.length === 0 && <tr><td colSpan={12} className="left muted">No closed trades yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
