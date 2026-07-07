"""Stock universe management.

Ships with an embedded NIFTY 50 / NIFTY 100 constituent list (as of early
2026 — refresh via data/nifty*.txt, one symbol per line). NIFTY 500 falls back
to the largest embedded list plus whatever the broker instrument master
provides; to use the true index list, drop the official NSE CSV symbols into
backend/app/data/nifty500.txt.
"""
from __future__ import annotations

import functools
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT",
    "M&M", "MARUTI", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
]

NIFTY100_EXTRA = [
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM",
    "BAJAJHLDNG", "BANKBARODA", "BERGEPAINT", "BOSCHLTD", "BPCL",
    "BRITANNIA", "CANBK", "CHOLAFIN", "COLPAL", "DABUR",
    "DIVISLAB", "DLF", "DMART", "GAIL", "GODREJCP",
    "HAVELLS", "HAL", "ICICIGI", "ICICIPRULI", "INDIGO",
    "IOC", "IRFC", "JINDALSTEL", "LICI", "LODHA",
    "LTIM", "MARICO", "MOTHERSON", "NAUKRI", "PFC",
    "PIDILITIND", "PNB", "RECLTD", "SIEMENS", "SRF",
    "TATAPOWER", "TORNTPHARM", "TVSMOTOR", "UNITDSPR", "VBL",
    "VEDL", "ZYDUSLIFE", "SHREECEM", "PGHH", "APLAPOLLO",
]

INDEX_SYMBOL = "NIFTY 50"


def _load_file(name: str) -> list[str] | None:
    p = DATA_DIR / name
    if p.exists():
        syms = [ln.strip().upper() for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
        return syms or None
    return None


@functools.lru_cache(maxsize=8)
def get_universe_symbols(universe: str) -> list[str]:
    universe = universe.upper()
    if universe == "NIFTY50":
        return _load_file("nifty50.txt") or list(NIFTY50)
    if universe == "NIFTY100":
        return _load_file("nifty100.txt") or list(dict.fromkeys(NIFTY50 + NIFTY100_EXTRA))
    if universe == "NIFTY500":
        return _load_file("nifty500.txt") or list(dict.fromkeys(NIFTY50 + NIFTY100_EXTRA))
    raise ValueError(f"unknown universe '{universe}'")
