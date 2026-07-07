# EB Swing Desk — NSE/BSE Swing Screener + Paper Trading

A swing-trading stock screener and paper-trading dashboard for the Indian market.
**FastAPI + PostgreSQL backend · React (Vite) frontend · Asia/Kolkata market-hours aware.**

> ⚠️ **Disclaimer:** Educational / simulated tool only. Not investment advice. All signals
> derive from technical indicators; past performance does not guarantee future results.

## Core principle — accuracy is non-negotiable

Every buy/sell/stop-loss/target number on screen comes from deterministic technical math
(`backend/app/engine/`), never from an LLM. Claude only **re-ranks** pre-filtered candidates,
writes rationale and flags conflicts — and even its echoed numbers are integrity-checked
against the engine's values (tolerance 0.01) and discarded on mismatch. Every signal row
carries a full formula audit trail (hover the strategy badge and score in the UI).

## Architecture

```
backend/app/
  adapters/    BrokerAdapter ABC + AngelOne (SmartAPI/TOTP), Zerodha (Kite request-token), Mock
  engine/      indicators (EMA/RSI/MACD/ADX/ATR/BB/divergence/RS), regime filter,
               liquidity guards, config-driven strategies, signal math, composite scoring, scanner
  llm/         Claude ranking layer with strict JSON contract + backend validation firewall
  paper/       paper-trade engine: live LTP entries, auto-exit loop, trailing stops, cost model
  backtest/    bar-by-bar backtester + shared metrics (SAME strategy/signal/cost code as live)
  services/    OHLC cache (PostgreSQL), universe (NIFTY 50/100/500), DB-backed settings
  api/         REST routes
frontend/      React dashboard: Signals | Open Positions | Closed Trades | Performance |
               Backtest | Watchlist | Settings, candlestick detail charts (lightweight-charts)
deploy/        nginx + systemd units for a single Ubuntu server behind Cloudflare
```

### The algorithm (deterministic core)

1. **Market regime gatekeeper** — NIFTY 50 vs EMA200 + ADX/DI classifies bullish / neutral /
   bearish. Aggressive longs (pullback, breakout) are suppressed in a confirmed downtrend.
   Shown as a banner.
2. **Liquidity & quality guards** — min ₹10 cr avg turnover, min ₹50 price, ≥200 candles
   (no recent listings), circuit-lock / zero-volume detection. Kills junk before scoring.
3. **Multi-timeframe confirmation** — daily setups only fire when the weekly trend agrees
   (weekly EMA20 > EMA50, computed on *completed* weeks — no lookahead).
4. **Three config-driven presets** (tunable in Settings, no code edits):
   Trend Pullback · Breakout · Mean Reversion (uptrends only).
5. **Signal math** — SL = max(entry − 1.5×ATR, recent swing low) i.e. the tighter stop;
   targets at 2R/3R; setups with effective (headroom) R:R < 1.5 or SL distance > 8% rejected;
   position size = capital × risk% / R.
6. **Composite score 0–100** — weighted trend/momentum/volume/relative-strength/R:R-quality
   sub-scores with penalties for bearish RSI divergence, weekly conflict, over-extension.
7. **Backtest == live** — the backtester iterates the *same* `evaluate_strategies` +
   `build_levels` + `apply_costs` functions bar-by-bar (next-open entries, stop-first fills,
   brokerage + slippage), so backtest results validate exactly the code that produces live signals.

## Quick start (dev, zero credentials)

The **mock broker** generates deterministic synthetic OHLCV so the entire system —
scans, charts, paper trading, auto-exits, backtests — works with no API keys.

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                      # defaults to sqlite for dev
uvicorn app.main:app --reload             # http://localhost:8000

# Frontend (separate shell)
cd frontend
npm install
npm run dev                               # http://localhost:5173 (proxies /api)
```

Open the dashboard → **Run Scan** → signals appear → **Paper Buy** → watch Open Positions
update live → the auto-exit engine closes trades at SL/target.

Run tests: `cd backend && pytest` (indicator math, signal math, LLM validation firewall,
end-to-end API flow).

## Real brokers

Pick the active broker in **Settings**; credentials are **server-side only** (`.env`).

- **Angel One** — `pip install smartapi-python`, set `ANGEL_API_KEY / ANGEL_CLIENT_CODE /
  ANGEL_PASSWORD / ANGEL_TOTP_SECRET`, click *Authenticate broker* (TOTP login).
- **Zerodha** — `pip install kiteconnect`, set `KITE_API_KEY / KITE_API_SECRET`. The top bar
  shows the Kite login URL; after login paste the `request_token` in Settings and authenticate.

Historical OHLC is cached in PostgreSQL and only the missing tail is fetched (rate-limit
friendly, throttled ~3 req/s). LTP quotes are batched. Broker/auth failures, holidays and
per-symbol errors degrade gracefully — they never crash a scan.

## LLM layer (optional)

Set `ANTHROPIC_API_KEY` in `.env`. The top-20 candidates (all numbers pre-computed) go to
Claude (`claude-sonnet-4-6`, temperature 0.2, strict JSON). The backend then: retries once on
parse failure and falls back to deterministic ordering; asserts every echoed price equals the
engine value (else discards + logs tampering); drops hallucinated symbols; re-appends dropped
candidates; sanitizes rationale text. Displayed prices **always** come from the engine.

## Universe

Embedded NIFTY 50/100 constituent lists ship in `services/universe.py`. To use the true
NIFTY 500 list, put one symbol per line in `backend/app/data/nifty500.txt`
(from NSE's official CSV). `nifty50.txt` / `nifty100.txt` override the embedded lists the same way.

## Production deploy (Ubuntu + Cloudflare)

```bash
sudo apt install python3.12-venv nginx postgresql
sudo -u postgres createuser swingdesk -P && sudo -u postgres createdb swingdesk -O swingdesk

# App at /opt/swingdesk
cd /opt/swingdesk/backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # set DATABASE_URL=postgresql+psycopg2://... , keys, CORS_ORIGINS
cd ../frontend && npm ci && npm run build

sudo cp deploy/swingdesk.service /etc/systemd/system/
sudo systemctl enable --now swingdesk
sudo cp deploy/nginx.conf /etc/nginx/sites-available/swingdesk
sudo ln -s /etc/nginx/sites-available/swingdesk /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Point a proxied (orange-cloud) Cloudflare DNS record at the server, set SSL mode
*Full (strict)* with a Cloudflare Origin Certificate at `/etc/ssl/cloudflare/`.

Scheduled scans: enable in Settings (IST cron, default 15:45 Mon–Fri, holiday-aware).

## Settings reference

Everything tunable lives in the Settings tab (DB-backed): capital & risk % per trade,
ATR stop multiplier, swing-low lookback, min R:R, liquidity thresholds, brokerage & slippage,
trailing-stop ATR multiplier, auto-exit poll interval, scan schedule, LLM toggle, and every
per-strategy parameter (RSI bands, ADX minimums, volume multiple, breakout lookback, …).
