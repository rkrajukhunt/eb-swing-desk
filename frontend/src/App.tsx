import { useCallback, useEffect, useState } from "react";
import { api, withRetry } from "./api";
import type { BrokerStatus, ScanLatest } from "./types";
import Backtest from "./components/Backtest";
import ClosedTrades from "./components/ClosedTrades";
import Options from "./components/Options";
import Performance from "./components/Performance";
import Positions from "./components/Positions";
import Settings from "./components/Settings";
import Signals from "./components/Signals";
import Watchlist from "./components/Watchlist";

const TABS = ["Signals", "Options", "Open Positions", "Closed Trades", "Performance", "Backtest", "Watchlist", "Settings"] as const;
type Tab = (typeof TABS)[number];

interface RegimeInfo {
  regime: string;
  detail: Record<string, any>;
  allows_longs: boolean;
}

export default function App() {
  const [tab, setTab] = useState<Tab>("Signals");
  const [regime, setRegime] = useState<RegimeInfo | null>(null);
  const [broker, setBroker] = useState<BrokerStatus | null>(null);
  const [scan, setScan] = useState<ScanLatest | null>(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadHeader = useCallback(async () => {
    try {
      const [r, b] = await withRetry(() => Promise.all([
        api.get<RegimeInfo>("/api/regime"),
        api.get<BrokerStatus>("/api/broker/status"),
      ]));
      setRegime(r);
      setBroker(b);
      setError(null);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }, []);

  const loadScan = useCallback(async () => {
    try {
      setScan(await withRetry(() => api.get<ScanLatest>("/api/scan/latest")));
      setError(null);
    } catch (e: any) {
      setError(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    loadHeader();
    loadScan();
  }, [loadHeader, loadScan]);

  const runScan = async (strategy: string) => {
    setScanning(true);
    setError(null);
    try {
      await api.post("/api/scan/run", { strategy, refresh: true });
      await Promise.all([loadScan(), loadHeader()]);
    } catch (e: any) {
      setError(`Scan failed: ${e.message || e}`);
    } finally {
      setScanning(false);
    }
  };

  const regimeClass = `regime-banner regime-${regime?.regime ?? "unknown"}`;
  const regimeText = regime
    ? `NIFTY regime: ${regime.regime.toUpperCase()}${regime.detail?.adx != null ? ` · ADX ${regime.detail.adx}` : ""}${
        regime.detail?.above_ema200 != null ? (regime.detail.above_ema200 ? " · above EMA200" : " · below EMA200") : ""
      }`
    : "NIFTY regime: …";

  return (
    <>
      <div className="topbar">
        <div className="brand">EB <span>Swing Desk</span></div>
        <div className={regimeClass} title={JSON.stringify(regime?.detail ?? {}, null, 2)}>{regimeText}</div>
        {regime && !regime.allows_longs && (
          <div className="regime-banner regime-bearish">Aggressive longs suppressed</div>
        )}
        <div className="broker-chip">
          Broker: <b className={broker?.authenticated ? "ok" : "bad"}>{broker?.name ?? "…"}</b>
          {" — "}{broker?.detail}
          {broker?.login_url && !broker.authenticated && (
            <> · <a href={broker.login_url} target="_blank" rel="noreferrer" style={{ color: "var(--blue)" }}>Kite login</a></>
          )}
        </div>
      </div>

      <div className="container">
        <div className="tabs">
          {TABS.map((t) => (
            <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
              {t}
            </div>
          ))}
        </div>

        {error && <div className="error-bar" onClick={() => setError(null)}>{error} (click to dismiss)</div>}

        {tab === "Signals" && (
          <Signals scan={scan} scanning={scanning} onRunScan={runScan} onReload={loadScan} />
        )}
        {tab === "Options" && <Options />}
        {tab === "Open Positions" && <Positions />}
        {tab === "Closed Trades" && <ClosedTrades />}
        {tab === "Performance" && <Performance />}
        {tab === "Backtest" && <Backtest />}
        {tab === "Watchlist" && <Watchlist />}
        {tab === "Settings" && <Settings onSaved={() => { loadHeader(); }} />}

        <div className="disclaimer">
          ⚠️ {scan?.disclaimer ??
            "Educational / simulated tool only. Not investment advice. Signals derive from technical indicators; past performance does not guarantee future results."}
        </div>
      </div>
    </>
  );
}
