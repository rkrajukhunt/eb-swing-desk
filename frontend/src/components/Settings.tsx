import { useEffect, useState } from "react";
import { api } from "../api";

export default function Settings({ onSaved }: { onSaved: () => void }) {
  const [s, setS] = useState<any | null>(null);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [authMsg, setAuthMsg] = useState<string | null>(null);
  const [requestToken, setRequestToken] = useState("");

  useEffect(() => {
    api.get<any>("/api/settings").then(setS).catch((e) => setErr(String(e.message || e)));
  }, []);

  if (!s) return <div className="panel muted">Loading…</div>;

  const set = (key: string, value: any) => setS({ ...s, [key]: value });
  const setStrat = (name: string, key: string, value: any) =>
    setS({ ...s, strategies: { ...s.strategies, [name]: { ...s.strategies[name], [key]: value } } });

  const save = async () => {
    setErr(null);
    try {
      const updated = await api.put<any>("/api/settings", s);
      setS(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      onSaved();
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  const authenticate = async () => {
    setAuthMsg(null);
    try {
      const body: any = { broker: s.active_broker };
      if (s.active_broker === "zerodha" && requestToken) body.request_token = requestToken;
      const r = await api.post<any>("/api/broker/authenticate", body);
      setAuthMsg(`${r.name}: ${r.authenticated ? "authenticated ✓" : "failed"} — ${r.detail}`);
      onSaved();
    } catch (e: any) {
      setAuthMsg(`Auth failed: ${e.message || e}`);
    }
  };

  const num = (key: string, label: string, step = 0.1) => (
    <label className="field" key={key}>{label}
      <input type="number" step={step} value={s[key]} onChange={(e) => set(key, parseFloat(e.target.value))} />
    </label>
  );

  return (
    <div className="panel">
      {err && <div className="error-bar">{err}</div>}

      <h4 className="section">Broker & data</h4>
      <div className="toolbar">
        <label className="field">Active broker
          <select value={s.active_broker} onChange={(e) => set("active_broker", e.target.value)}>
            <option value="mock">Mock (synthetic data, no keys needed)</option>
            <option value="angel_one">Angel One SmartAPI</option>
            <option value="zerodha">Zerodha Kite Connect</option>
          </select>
        </label>
        <label className="field">Universe
          <select value={s.universe} onChange={(e) => set("universe", e.target.value)}>
            <option>NIFTY50</option><option>NIFTY100</option><option>NIFTY500</option>
          </select>
        </label>
        {s.active_broker === "zerodha" && (
          <label className="field">Kite request_token
            <input type="text" value={requestToken} onChange={(e) => setRequestToken(e.target.value)} />
          </label>
        )}
        <button className="secondary" onClick={authenticate}>Authenticate broker</button>
      </div>
      {authMsg && <div className="muted" style={{ marginBottom: 10 }}>{authMsg}</div>}
      <div className="muted" style={{ fontSize: 12, marginBottom: 16 }}>
        API keys live server-side in <code>.env</code> only (ANGEL_*, KITE_*) — they are never exposed to this UI.
      </div>

      <h4 className="section">Risk & sizing</h4>
      <div className="toolbar">
        {num("capital", "Capital (₹)", 1000)}
        {num("risk_pct_per_trade", "Risk % per trade", 0.1)}
        {num("min_rr", "Min effective R:R", 0.1)}
        {num("max_risk_pct_of_price", "Max SL distance (% of price)", 0.5)}
      </div>

      <h4 className="section">Signal math</h4>
      <div className="toolbar">
        {num("atr_stop_mult", "ATR stop multiplier", 0.1)}
        {num("swing_low_lookback", "Swing-low lookback (bars)", 1)}
        {num("trailing_atr_mult", "Trailing ATR multiplier", 0.1)}
      </div>

      <h4 className="section">Liquidity guards</h4>
      <div className="toolbar">
        {num("min_avg_turnover_cr", "Min avg turnover (₹ cr)", 1)}
        {num("min_price", "Min price (₹)", 5)}
        {num("min_history_candles", "Min history (candles)", 10)}
      </div>

      <h4 className="section">Costs & slippage</h4>
      <div className="toolbar">
        {num("brokerage_per_order", "Brokerage / order (₹)", 1)}
        {num("slippage_pct", "Slippage % per leg", 0.01)}
      </div>

      <h4 className="section">Scan schedule & engine</h4>
      <div className="toolbar">
        <div className="checkbox-row">
          <input id="sched" type="checkbox" checked={s.scan_schedule_enabled}
                 onChange={(e) => set("scan_schedule_enabled", e.target.checked)} />
          <label htmlFor="sched">Scheduled scans (IST cron)</label>
        </div>
        <label className="field">Cron (min hr dom mon dow)
          <input type="text" value={s.scan_schedule_cron} onChange={(e) => set("scan_schedule_cron", e.target.value)} />
        </label>
        {num("auto_exit_poll_seconds", "Auto-exit poll (s)", 5)}
        <div className="checkbox-row">
          <input id="llm" type="checkbox" checked={s.llm_enabled} onChange={(e) => set("llm_enabled", e.target.checked)} />
          <label htmlFor="llm">LLM re-ranking (Claude — additive only, deterministic fallback)</label>
        </div>
      </div>

      <h4 className="section">Strategy presets</h4>
      <div className="subgrid">
        {Object.entries(s.strategies).map(([name, params]: [string, any]) => (
          <div key={name} className="panel" style={{ marginBottom: 0 }}>
            <div className="checkbox-row" style={{ marginBottom: 10 }}>
              <input id={`en-${name}`} type="checkbox" checked={params.enabled}
                     onChange={(e) => setStrat(name, "enabled", e.target.checked)} />
              <label htmlFor={`en-${name}`}><b>{name}</b></label>
            </div>
            <div className="toolbar">
              {Object.entries(params).filter(([k]) => k !== "enabled").map(([k, v]) => (
                typeof v === "boolean" ? (
                  <div className="checkbox-row" key={k}>
                    <input id={`${name}-${k}`} type="checkbox" checked={v as boolean}
                           onChange={(e) => setStrat(name, k, e.target.checked)} />
                    <label htmlFor={`${name}-${k}`}>{k}</label>
                  </div>
                ) : (
                  <label className="field" key={k}>{k}
                    <input type="number" step="0.5" value={v as number}
                           onChange={(e) => setStrat(name, k, parseFloat(e.target.value))} />
                  </label>
                )
              ))}
            </div>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 20 }}>
        <button onClick={save}>{saved ? "Saved ✓" : "Save settings"}</button>
      </div>
    </div>
  );
}
