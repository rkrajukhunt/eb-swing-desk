import { useEffect, useState } from "react";
import { api } from "../api";
import ChartModal from "./ChartModal";

interface Item { id: number; symbol: string; note: string; added_at: string }

export default function Watchlist() {
  const [items, setItems] = useState<Item[]>([]);
  const [symbol, setSymbol] = useState("");
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [chartSymbol, setChartSymbol] = useState<string | null>(null);

  const load = () =>
    api.get<{ items: Item[] }>("/api/watchlist").then((d) => setItems(d.items)).catch((e) => setErr(String(e.message || e)));

  useEffect(() => { load(); }, []);

  const add = async () => {
    if (!symbol.trim()) return;
    try {
      await api.post("/api/watchlist", { symbol: symbol.trim(), note });
      setSymbol(""); setNote(""); setErr(null);
      load();
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  };

  const remove = async (id: number) => { await api.del(`/api/watchlist/${id}`); load(); };

  return (
    <div className="panel">
      {err && <div className="error-bar">{err}</div>}
      <div className="toolbar">
        <label className="field">Symbol
          <input type="text" value={symbol} placeholder="e.g. TCS" onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
        </label>
        <label className="field">Note
          <input type="text" value={note} onChange={(e) => setNote(e.target.value)} style={{ width: 260 }} />
        </label>
        <button onClick={add}>Add</button>
      </div>
      <table>
        <thead><tr><th className="left">Symbol</th><th className="left">Note</th><th className="left">Added</th><th></th></tr></thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id} onClick={() => setChartSymbol(i.symbol)}>
              <td className="left"><b>{i.symbol}</b></td>
              <td className="left">{i.note}</td>
              <td className="left muted">{new Date(i.added_at + "Z").toLocaleDateString()}</td>
              <td><button className="small danger" onClick={(e) => { e.stopPropagation(); remove(i.id); }}>Remove</button></td>
            </tr>
          ))}
          {items.length === 0 && <tr><td colSpan={4} className="left muted">Watchlist is empty.</td></tr>}
        </tbody>
      </table>
      {chartSymbol && <ChartModal symbol={chartSymbol} onClose={() => setChartSymbol(null)} />}
    </div>
  );
}
