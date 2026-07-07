"""Application configuration.

Environment-level secrets live here (loaded from .env). Tunable trading
parameters live in the settings store (DB-backed, editable from the UI) —
see services/settings_store.py. DEFAULT_SETTINGS below is the single source
of defaults for those.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

IST = "Asia/Kolkata"


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./swingdesk.db"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173"

    anthropic_api_key: str = ""

    angel_api_key: str = ""
    angel_client_code: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""

    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""


env = EnvSettings()


# ---------------------------------------------------------------------------
# User-tunable settings (persisted in DB, editable from the Settings tab).
# Every number that shapes a signal is here — nothing is hard-coded in the
# strategy engine.
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: dict = {
    "active_broker": "mock",              # mock | angel_one | zerodha
    "universe": "NIFTY100",               # NIFTY50 | NIFTY100 | NIFTY500
    # --- Position sizing ---
    "capital": 500_000.0,                 # simulated capital (₹)
    "risk_pct_per_trade": 1.0,            # max % of capital risked per trade
    # --- Liquidity & quality guards ---
    "min_avg_turnover_cr": 10.0,          # min 20d avg daily turnover in ₹ crore
    "min_price": 50.0,                    # reject penny-ish stocks
    "min_history_candles": 200,           # reject recently-listed names
    # --- Signal math ---
    "atr_stop_mult": 1.5,                 # SL = max(entry - ATR*mult, swing low)
    "swing_low_lookback": 10,             # bars for recent swing low
    "min_rr": 1.5,                        # reject setups whose headroom R:R < this
    "max_risk_pct_of_price": 8.0,         # reject if SL distance > this % of entry
    # --- Costs (so paper results aren't fantasy) ---
    "brokerage_per_order": 20.0,          # flat ₹ per executed order
    "slippage_pct": 0.05,                 # % applied adversely on entry AND exit
    # --- Paper engine ---
    "auto_exit_poll_seconds": 20,
    "trailing_stop_default": False,
    "trailing_atr_mult": 2.0,             # trail SL at (highest close - ATR*mult)
    # --- Scheduler (cron, IST) ---
    "scan_schedule_enabled": False,
    "scan_schedule_cron": "45 15 * * 1-5",   # 15:45 IST Mon-Fri (post close)
    # --- LLM layer ---
    "llm_enabled": True,
    "llm_model": "claude-sonnet-4-6",
    "llm_max_candidates": 20,
    # --- Backtest ---
    "backtest_years": 3,
    "backtest_max_holding_days": 40,
    # --- Strategy presets (config-driven; tune without code edits) ---
    "strategies": {
        "trend_pullback": {
            "enabled": True,
            "rsi_min": 40.0,
            "rsi_max": 60.0,
            "require_rsi_rising": True,
            "adx_min": 20.0,
            "pullback_max_dist_ema20_pct": 2.5,   # close within this % of EMA20/50
            "rs_min": 0.0,                        # 55d relative strength vs NIFTY (pct pts)
        },
        "breakout": {
            "enabled": True,
            "breakout_lookback": 20,              # close breaks N-day high
            "vol_mult_min": 1.5,                  # volume vs 20d avg
            "adx_min": 25.0,
            "rsi_min": 55.0,
            "rsi_max": 72.0,
            "rs_min": 0.0,
        },
        "mean_reversion": {
            "enabled": True,
            "rsi_max": 30.0,
            "require_lower_bb": True,
            "require_ema200_rising": True,        # only in long-term uptrends
        },
    },
}

DISCLAIMER = (
    "Educational / simulated tool only. Not investment advice. Signals are derived "
    "from technical indicators; past performance does not guarantee future results. "
    "Verify independently before trading real money."
)
