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

    # --- LLM providers (each speaks its own official SDK; no base_url shims) ---
    # Which key is read depends on the `llm_provider` setting; see LLM_PROVIDERS.
    anthropic_api_key: str = ""       # sk-ant-...   → llm_provider = "anthropic"
    openrouter_api_key: str = ""      # sk-or-v1-... → llm_provider = "openrouter"

    angel_api_key: str = ""
    angel_client_code: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""

    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""


env = EnvSettings()


# ---------------------------------------------------------------------------
# LLM providers. Each is reached through its own official SDK — `anthropic` via
# the Anthropic Messages API, `openrouter` via the `openrouter` package. `sdk`
# names the pip package; `key_env` names the EnvSettings field holding its key.
#
# NOTE: model ids are NOT portable across providers. Anthropic uses dashes
# ("claude-sonnet-4-6"); OpenRouter namespaces + dots ("anthropic/claude-sonnet-4.6").
# ---------------------------------------------------------------------------
LLM_PROVIDERS: dict = {
    "anthropic": {
        "label": "Anthropic (Messages API)",
        "sdk": "anthropic",
        "key_env": "anthropic_api_key",
        "key_prefix": "sk-ant-",
        "models": [
            "claude-opus-4-8",
            "claude-sonnet-5",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
        ],
    },
    "openrouter": {
        "label": "OpenRouter (official SDK)",
        "sdk": "openrouter",
        "key_env": "openrouter_api_key",
        "key_prefix": "sk-or-",
        "models": [
            "anthropic/claude-opus-4.8",
            "anthropic/claude-sonnet-5",
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-haiku-4.5",
        ],
    },
}


# ---------------------------------------------------------------------------
# User-tunable settings (persisted in DB, editable from the Settings tab).
# Every number that shapes a signal is here — nothing is hard-coded in the
# strategy engine.
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: dict = {
    "active_broker": "yahoo",             # yahoo | angel_one | zerodha
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
    "llm_provider": "anthropic",          # anthropic | openrouter (see LLM_PROVIDERS)
    "llm_model": "claude-sonnet-4-6",     # must match the provider's id format
    "llm_max_candidates": 20,
    # --- Backtest ---
    "backtest_years": 3,
    "backtest_max_holding_days": 40,
    # --- Option selling (NIFTY weekly income module — separate from swing) ---
    "options": {
        "enabled": True,
        "underlying": "NIFTY 50",
        "lot_size": 75,                    # NIFTY lot size (verify with your broker)
        "strike_step": 50,
        "expiry_weekday": 1,               # 0=Mon … 6=Sun. NIFTY weekly expiry = Tuesday
        "risk_free_rate_pct": 6.5,         # for Black-Scholes
        "capital_allocation_pct": 60.0,    # % of capital deployable as option margin
        # Entry timing (the "when to sell")
        "entry_dte_min": 1,                # don't open with <1 day left
        "entry_dte_max": 7,                # sell the current weekly cycle only
        "min_iv_pct": 9.0,                 # skip selling when implied vol is too thin
        # Strike selection (the "what to sell / what to buy as hedge")
        "em_multiplier": 1.1,              # start short strikes beyond 1.1× expected move
        "em_multiplier_floor": 0.5,        # never tighter than 0.5× EM
        "max_short_delta": 0.35,           # hard cap on short-strike delta
        "wing_width_points": 200,          # hedge (bought) leg distance
        "weekly_roc_target_pct": 2.0,      # tighten strikes until ROC ≥ this (bounded)
        "min_credit_points": 8.0,          # reject dust credits
        # Exits (auto-managed by the paper engine)
        "profit_take_pct_of_max": 60.0,    # close at 60% of max profit captured
        "stop_loss_mult_of_credit": 2.0,   # close when loss = 2× credit received
        "exit_on_short_strike_breach": True,
        # Costs
        "brokerage_per_leg": 20.0,         # per leg per side (₹)
        "slippage_pct_premium": 1.0,       # % of each leg's premium, adverse, both sides
    },
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
