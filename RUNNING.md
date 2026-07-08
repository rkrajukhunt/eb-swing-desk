# Running EB Swing Desk locally

Two processes: a **FastAPI backend** and a **Vite/React frontend**. The frontend proxies
`/api/*` to the backend, so you must start the backend first.

The app runs end-to-end with **zero credentials** — the mock broker generates deterministic
synthetic OHLCV, so scans, charts, paper trading, auto-exits and backtests all work offline.

---

## Prerequisites

| Tool | Version used | Check |
|---|---|---|
| Python | 3.12 | `python3 --version` |
| Node.js | 22.x | `node --version` |
| npm | 11.x | `npm --version` |

PostgreSQL is only needed for production. Local dev uses SQLite.

---

## 1. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Set the database

`.env.example` ships with a **PostgreSQL** URL. For local dev, edit `.env`:

```ini
DATABASE_URL=sqlite:///./swingdesk.db
```

> The README claims `.env.example` "defaults to sqlite for dev". It does not — you must change
> this line yourself, or the app will fail to connect to a Postgres server you haven't started.

### Start it

```bash
uvicorn app.main:app --reload          # http://localhost:8000
```

Verify:

```bash
curl http://localhost:8000/api/health          # {"ok":true}
open http://localhost:8000/docs                # interactive OpenAPI
```

### Port 8000 already in use?

Run on another port and tell Vite where to find it:

```bash
uvicorn app.main:app --reload --port 8001
```

Then create `frontend/vite.config.local.ts` (the committed `vite.config.ts` hardcodes `:8000`):

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8001" } },
});
```

and start the frontend with `npx vite --config vite.config.local.ts`.

---

## 2. Frontend

In a **separate shell**:

```bash
cd frontend
npm install
npm run dev                            # http://localhost:5173
```

If npm blocks esbuild's postinstall (`npm warn allow-scripts`), esbuild usually still works
because the platform binary package (`@esbuild/linux-x64`) is installed. Confirm with:

```bash
node -e "require('esbuild')" && echo OK
```

Only if that fails: `npm approve-scripts esbuild`.

---

## 3. First run

Open **http://localhost:5173** and click **Run Scan**.

On a fresh database the regime banner reads `unknown` until the first scan populates the
OHLC cache — `classify_regime()` needs ≥ 210 NIFTY 50 candles. This is expected, not a bug.
After the scan it flips to `bullish` / `neutral` / `bearish`.

A full NIFTY100 scan takes ~30 s on the mock broker (cold cache).

Then: **Paper Buy** a signal → watch **Open Positions** update live → the server-side
auto-exit engine closes it at SL/target.

---

## 4. Tests

```bash
cd backend && .venv/bin/python -m pytest -q      # 73 tests
```

Covers indicator math, signal math, the LLM validation firewall, provider/model-dialect
resolution, and end-to-end API flow.
You'll see `DeprecationWarning: datetime.utcnow()` — harmless today, will break on a future Python.

---

## 5. Optional: LLM ranking layer

The engine is fully deterministic without this. Claude only **re-ranks** pre-computed
candidates and writes rationale; every echoed price is checked against the engine's value
(tolerance `0.01`) and discarded on mismatch. With no key, you get deterministic ordering
and lose nothing structural.

Each provider uses its **own official SDK** — there is no `base_url` override. Put the key for
the provider you want in `.env`, pick it in **Settings → LLM re-ranking**, then click
**Test LLM connection**.

| Provider | SDK (`pip`) | Key variable | Model id format |
|---|---|---|---|
| **Anthropic** | `anthropic` | `ANTHROPIC_API_KEY` (`sk-ant-…`) | `claude-sonnet-4-6` |
| **OpenRouter** | `openrouter` | `OPENROUTER_API_KEY` (`sk-or-v1-…`) | `anthropic/claude-sonnet-4.6` |

Both ship in `requirements.txt`.

```ini
# backend/.env — only the key for the provider you use
ANTHROPIC_API_KEY=
OPENROUTER_API_KEY=sk-or-v1-...
```

> **Model ids are not portable.** Anthropic uses bare dashed ids (`claude-sonnet-4-6`);
> OpenRouter namespaces with dots (`anthropic/claude-sonnet-4.6`). Sending one to the other
> 404s. Switching the provider dropdown auto-selects that provider's first preset, and
> `POST /api/llm/test` rejects a mismatched dialect before spending a request.

An OpenRouter key is **not** an Anthropic key. Because the scan path swallows every LLM error
and falls back to deterministic ordering, a wrong key or model produces **no error at all** —
just no rationale. That is what **Test LLM connection** exists for:

```bash
curl -X POST http://localhost:8001/api/llm/test
# {"ok":true,"provider":"openrouter","key_env":"OPENROUTER_API_KEY",
#  "model":"anthropic/claude-sonnet-4.6","detail":"replied 'OK' · 16 in / 4 out · $0.000108"}
```

Or configure it entirely over the API (`GET /api/meta` lists providers + model presets):

```bash
curl -X PUT http://localhost:8001/api/settings -H 'Content-Type: application/json' \
  -d '{"llm_enabled":true,"llm_provider":"openrouter","llm_model":"anthropic/claude-sonnet-4.6"}'
```

**Sampling params.** Anthropic removed `temperature`/`top_p`/`top_k` on Opus 4.7+, Sonnet 5 and
Fable 5 — sending them is a 400. `providers.supports_temperature()` withholds `temperature` for
those models on both providers, so you can select any preset safely.

**Credit ceilings (`HTTP 402`).** `max_tokens` is a **reservation**, not a bill: providers
check your balance against it *before* generating, even though the reply usually needs far
less (a 2-candidate ranking reserves 1312 but uses ~700). On a credit-capped account this
rejects a request you could actually afford.

The ranker handles it: on a 402 it reads the ceiling the provider names
(`"can only afford 1143"`) and retries once at that ceiling — provided it still fits every
candidate (≥ 300 tokens each). If the ceiling is below that floor, it stops immediately with
an actionable log line instead of paying for a truncated reply:

```
LLM budget capped at 963 tokens (wanted 1312) — retrying at the cap     ← recovered
LLM ranking unavailable — insufficient credits for 20 candidates
  (provider allows 400 tokens, need ~6000). Add credits or lower llm_max_candidates.
```

Check your balance: `curl -H "Authorization: Bearer $OPENROUTER_API_KEY" https://openrouter.ai/api/v1/credits`

Confirm it engaged: the Signals header shows **`LLM-ranked ✓`**, and `GET /api/scan/latest`
returns `run.llm_used: true`.

---

## 6. Optional: real broker data

Credentials are **server-side only** (`.env`) and never reach the frontend. This app has
**no order-placement code** — `BrokerAdapter` exposes only reads (`get_ltp`,
`get_historical_ohlc`, `get_option_quotes`). It cannot trade your real money.

### Angel One (SmartAPI)

```bash
pip install smartapi-python logzero websocket-client
```

> `smartapi-python` imports `logzero` and `websocket` but declares neither, so
> `pip install smartapi-python` alone leaves you with `ModuleNotFoundError`.

```ini
ANGEL_API_KEY=...
ANGEL_CLIENT_CODE=...
ANGEL_PASSWORD=1234          # your 4-digit MPIN, despite the field name
ANGEL_TOTP_SECRET=...        # the base32 seed, not the 6-digit code
```

Settings → broker `angel_one` → **Authenticate broker**.

> ⚠️ **You get 5 MPIN attempts before Angel One locks the account.** A wrong value returns
> `INVALID MPIN. Attempt N/5`. Verify your MPIN in the Angel One app *before* retrying here.
> To check the TOTP seed without spending an attempt:
> `python -c "import pyotp; print(pyotp.TOTP('YOUR_SEED').now())"` and compare to your
> authenticator app. If it matches, the seed is fine and the MPIN is the problem.

### Zerodha (Kite Connect)

```bash
pip install kiteconnect
```

```ini
KITE_API_KEY=...
KITE_API_SECRET=...
```

Settings → broker `zerodha` → open the login URL from the top bar → log in → paste the
`request_token` back into Settings → **Authenticate**.

> Kite access tokens expire each morning (~6 am IST) **and** are held in process memory, so a
> backend restart also loses them. Expect to redo this daily.

---

## 7. Switching from mock to a real broker — wipe the cache first

`ohlc_candles` is keyed on `(symbol, interval, ts)` with **no broker column**, and
`refresh_symbol()` short-circuits when the newest cached candle is less than a day old:

```python
if (now - last).days < 1:
    return 0        # fetches nothing
```

So if mock data is already cached with today's timestamp, switching to Angel One / Zerodha
fetches **nothing** — your scan silently runs on synthetic prices while the UI reports
`broker: angel_one`. Wipe before switching:

```bash
cd backend
cp swingdesk.db swingdesk.db.bak            # optional
sqlite3 swingdesk.db "DELETE FROM ohlc_candles; DELETE FROM signals; DELETE FROM scan_runs;"
```

**Also close any open mock paper trades.** Their entry prices are synthetic (e.g. PGHH @ ₹986
when the real price is ~₹10,000). On the next poll the auto-exit engine sees the real LTP far
above target and books a fabricated "Target Hit" profit into Performance.

```bash
sqlite3 swingdesk.db "DELETE FROM paper_trades WHERE broker_source='mock';"
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `address already in use` on `:8000` | Another service holds the port — see §1 |
| Regime banner shows `unknown` | Cold OHLC cache; run a scan |
| `ModuleNotFoundError: logzero` | `smartapi-python`'s undeclared deps — see §6 |
| No LLM rationale, no error | Bad key or wrong model dialect — run `POST /api/llm/test` |
| `HTTP 402 ... requires more credits` | Balance spent; ranker auto-retries at the allowed ceiling — see §5 |
| Scan runs on stale prices after broker switch | OHLC cache not wiped — see §7 |
| IDE reports `Cannot find module 'anthropic'` | VS Code is using system Python — select `backend/.venv/bin/python` as the interpreter |

---

## Ports

| Service | URL |
|---|---|
| Backend (FastAPI) | http://localhost:8000 · docs at `/docs` |
| Frontend (Vite) | http://localhost:5173 |

⚠️ Educational / simulated tool only. Not investment advice.
