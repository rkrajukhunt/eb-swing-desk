import { useState } from "react";
import { api, fmt } from "../api";
import type { SignalRow } from "../types";

export default function PaperBuyModal({
  signal, onClose, onDone,
}: { signal: SignalRow; onClose: () => void; onDone: () => void }) {
  // Pre-filled with the system's deterministic levels — all editable
  const [qty, setQty] = useState(signal.suggested_qty || 1);
  const [sl, setSl] = useState(signal.stop_loss);
  const [target, setTarget] = useState(signal.target_2r);
  const [trailing, setTrailing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const confirm = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api.post("/api/paper/trades", {
        symbol: signal.symbol,
        qty,
        stop_loss: sl,
        target,
        strategy: signal.strategy,
        signal_id: signal.id,
        trailing,
      });
      onDone();
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Paper Buy — {signal.symbol}</h3>
        <div className="muted" style={{ marginBottom: 12, fontSize: 12 }}>
          System levels: entry ref {fmt(signal.entry)} · SL {fmt(signal.stop_loss)} · 2R {fmt(signal.target_2r)} · 3R {fmt(signal.target_3r)}.
          Actual entry uses the <b>live LTP</b> at confirmation.
        </div>
        {err && <div className="error-bar">{err}</div>}
        <div className="row">
          <label className="field">Quantity
            <input type="number" min={1} value={qty} onChange={(e) => setQty(parseInt(e.target.value || "0"))} />
          </label>
          <label className="field">Stop-loss
            <input type="number" step="0.05" value={sl} onChange={(e) => setSl(parseFloat(e.target.value || "0"))} />
          </label>
          <label className="field">Target
            <input type="number" step="0.05" value={target} onChange={(e) => setTarget(parseFloat(e.target.value || "0"))} />
          </label>
        </div>
        <div className="row">
          <button className="secondary small" onClick={() => setTarget(signal.target_2r)}>Use 2R</button>
          <button className="secondary small" onClick={() => setTarget(signal.target_3r)}>Use 3R</button>
        </div>
        <div className="checkbox-row" style={{ marginBottom: 16 }}>
          <input id="trail" type="checkbox" checked={trailing} onChange={(e) => setTrailing(e.target.checked)} />
          <label htmlFor="trail">Trailing stop (ATR-based — SL ratchets up as price advances)</label>
        </div>
        <div className="row">
          <button onClick={confirm} disabled={busy || qty <= 0}>{busy ? "Placing…" : "Confirm Paper Buy"}</button>
          <button className="secondary" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
