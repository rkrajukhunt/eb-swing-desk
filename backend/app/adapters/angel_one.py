"""Angel One SmartAPI adapter (smartapi-python, TOTP login).

Credentials come from environment only (never the frontend). The smartapi
package is an optional dependency — importing this module without it installed
is fine; authenticate() reports the problem instead of crashing.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .base import BrokerAdapter, BrokerError, BrokerStatus, Candle, Instrument
from ..config import env

log = logging.getLogger(__name__)

_INTERVAL_MAP = {"day": "ONE_DAY", "week": "ONE_DAY"}  # weekly is resampled from daily

# getCandleData rate-limit handling (Angel: ~3 req/s, 180/min). Bursts trip it
# well before the nominal ceiling, so we space calls out AND retry on rejection.
_RATE_LIMIT_RETRIES = 6
_RATE_BACKOFF_BASE = 1.0          # 1s, 2s, 4s, 8s, 16s, 32s (capped below)
_RATE_BACKOFF_MAX = 20.0         # don't sleep longer than this on a single retry
_HISTORICAL_MIN_INTERVAL = 0.6   # min gap between the START of consecutive calls (~1.6/s)

# Angel signals the historical rate limiter with several different phrasings; a
# matcher that only knows one of them silently drops symbols on the others.
_RATE_LIMIT_MARKERS = ("exceeding access rate", "too many requests", "ab1021", "access denied")


def _is_rate_limited(x) -> bool:
    """True if an exception or response message signals Angel's rate limiter."""
    s = str(x).lower()
    return any(m in s for m in _RATE_LIMIT_MARKERS)


class AngelOneAdapter(BrokerAdapter):
    name = "angel_one"

    def __init__(self) -> None:
        self._smart = None
        self._instruments: dict[str, Instrument] = {}
        self._detail = "Not authenticated"
        self._next_call_at = 0.0   # monotonic clock floor for the next candle call

    def _pace(self) -> None:
        """Block until at least _HISTORICAL_MIN_INTERVAL has passed since the last
        candle call started. Enforcing the gap BEFORE each call (rather than a
        fixed sleep after) means retry backoffs and slow responses correctly push
        the next call later instead of letting bursts through."""
        now = time.monotonic()
        if now < self._next_call_at:
            time.sleep(self._next_call_at - now)
        self._next_call_at = time.monotonic() + _HISTORICAL_MIN_INTERVAL

    # ------------------------------------------------------------------
    def authenticate(self, **kwargs) -> BrokerStatus:
        try:
            from SmartApi import SmartConnect  # type: ignore
            import pyotp
        except ImportError as e:
            raise BrokerError(f"smartapi-python not installed: {e}") from e

        if not (env.angel_api_key and env.angel_client_code and env.angel_password and env.angel_totp_secret):
            raise BrokerError("Angel One credentials missing in environment (.env)")

        try:
            smart = SmartConnect(api_key=env.angel_api_key)
            totp = pyotp.TOTP(env.angel_totp_secret).now()
            data = smart.generateSession(env.angel_client_code, env.angel_password, totp)
            if not data or not data.get("status"):
                raise BrokerError(f"Angel One login failed: {data and data.get('message')}")
            self._smart = smart
            self._load_instruments()
            # Login is lenient about the API key (it checks client+MPIN+TOTP), but
            # the data endpoints validate it strictly. A key that logs in can still
            # be rejected by getCandleData/getMarketData with "Invalid API Key" —
            # e.g. when ANGEL_API_KEY holds the client code or a Publisher-only key.
            # Probe once so the button reports real usability, not just login.
            self._detail = self._probe_data_access()
            return self.status()
        except BrokerError:
            raise
        except Exception as e:  # network / SDK errors
            raise BrokerError(f"Angel One auth error: {e}") from e

    def _probe_data_access(self) -> str:
        """One cheap market-data call to confirm the API key has data scope."""
        try:
            token = self._instruments.get("NIFTY 50")
            token = token.token if token else next(
                (i.token for i in self._instruments.values() if i.token), "")
            if not token:
                return "Authenticated via TOTP (instrument master empty)"
            resp = self._smart.ltpData("NSE", "RELIANCE-EQ", "2885")
            if resp and resp.get("status"):
                return "Authenticated via TOTP — market data OK"
            msg = (resp or {}).get("message", "unknown")
            log.warning("Angel data-access probe failed: %s "
                        "(ANGEL_API_KEY may be your client code or a non-market-data key)", msg)
            return (f"Login OK but data endpoints reject the key: '{msg}'. "
                    f"Check ANGEL_API_KEY — it must be a Market-Feeds/Trading API key, "
                    f"not your client code.")
        except Exception as e:                       # noqa: BLE001
            log.warning("Angel data-access probe error: %s", e)
            return f"Authenticated via TOTP — data probe error: {str(e)[:80]}"

    def status(self) -> BrokerStatus:
        return BrokerStatus(name=self.name, authenticated=self._smart is not None, detail=self._detail)

    # ------------------------------------------------------------------
    def _load_instruments(self) -> None:
        """Instrument master (public JSON dump published by Angel One)."""
        import httpx

        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        try:
            rows = httpx.get(url, timeout=60).json()
        except Exception as e:
            log.warning("Angel instrument master fetch failed: %s", e)
            return
        for r in rows:
            if r.get("exch_seg") == "NSE" and r.get("symbol", "").endswith("-EQ"):
                sym = r["symbol"][:-3]
                self._instruments[sym] = Instrument(
                    symbol=sym, name=r.get("name", ""), exchange="NSE", token=r.get("token", "")
                )
            elif r.get("exch_seg") == "NSE" and r.get("name") == "NIFTY" and r.get("instrumenttype") == "AMXIDX":
                self._instruments["NIFTY 50"] = Instrument(
                    symbol="NIFTY 50", name="NIFTY 50", exchange="NSE", token=r.get("token", "")
                )

    def _require_auth(self):
        if self._smart is None:
            raise BrokerError("Angel One not authenticated — call authenticate first")
        return self._smart

    def _token_for(self, symbol: str) -> str:
        inst = self._instruments.get(symbol)
        if not inst or not inst.token:
            raise BrokerError(f"No Angel One token for symbol {symbol}")
        return inst.token

    # ------------------------------------------------------------------
    def get_historical_ohlc(self, symbol, interval, from_dt, to_dt) -> list[Candle]:
        smart = self._require_auth()
        params = {
            "exchange": "NSE",
            "symboltoken": self._token_for(symbol),
            "interval": _INTERVAL_MAP.get(interval, "ONE_DAY"),
            "fromdate": from_dt.strftime("%Y-%m-%d 09:15"),
            "todate": to_dt.strftime("%Y-%m-%d 15:30"),
        }
        # Angel throttles getCandleData (~3 req/s, 180/min) and rejects bursts well
        # under that with AB1021 ("Too many requests") or a raw "exceeding access
        # rate" body. Space every call out and retry rejections with capped
        # exponential backoff instead of dropping the symbol.
        resp = None
        last_err: str | None = None
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            self._pace()
            try:
                resp = smart.getCandleData(params)
            except Exception as e:
                if _is_rate_limited(e) and attempt < _RATE_LIMIT_RETRIES:
                    last_err = str(e)
                    time.sleep(min(_RATE_BACKOFF_BASE * (2 ** attempt), _RATE_BACKOFF_MAX))
                    continue
                raise BrokerError(f"Angel One candle fetch failed for {symbol}: {e}") from e
            if resp and _is_rate_limited(resp.get("message")) and attempt < _RATE_LIMIT_RETRIES:
                last_err = str(resp.get("message"))
                time.sleep(min(_RATE_BACKOFF_BASE * (2 ** attempt), _RATE_BACKOFF_MAX))
                continue
            break
        if not resp or not resp.get("status"):
            detail = (resp and resp.get("message")) or last_err or "no response"
            raise BrokerError(f"Angel One candle fetch failed for {symbol}: {detail}")
        candles = [
            Candle(
                ts=datetime.fromisoformat(row[0][:19]),
                open=float(row[1]), high=float(row[2]), low=float(row[3]),
                close=float(row[4]), volume=float(row[5]),
            )
            for row in (resp.get("data") or [])
        ]
        if interval == "week":
            from .yahoo import _resample_weekly

            candles = _resample_weekly(candles)
        return candles

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        smart = self._require_auth()
        out: dict[str, float] = {}
        # SmartAPI market-data endpoint accepts batches of tokens
        tokens = {}
        for s in symbols:
            try:
                tokens[self._token_for(s)] = s
            except BrokerError:
                continue
        if not tokens:
            return out
        try:
            resp = smart.getMarketData("LTP", {"NSE": list(tokens.keys())})
            for row in (resp.get("data", {}) or {}).get("fetched", []):
                sym = tokens.get(str(row.get("symbolToken")))
                if sym:
                    out[sym] = float(row.get("ltp"))
        except Exception as e:
            log.warning("Angel LTP batch failed: %s", e)
        return out

    def get_instruments(self) -> list[Instrument]:
        return list(self._instruments.values())

    # --- Options (NFO) --------------------------------------------------------
    def _nfo_options(self, underlying: str) -> list[dict]:
        """Cached NFO option rows from the scrip master for the underlying.
        Angel publishes strike in paise (strike*100) and expiry as DDMMMYYYY."""
        if not getattr(self, "_nfo_cache", None):
            import httpx

            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            try:
                rows = httpx.get(url, timeout=60).json()
            except Exception as e:
                raise BrokerError(f"Angel scrip master fetch failed: {e}") from e
            name = underlying.replace(" 50", "").strip()
            self._nfo_cache = [
                r for r in rows
                if r.get("exch_seg") == "NFO" and r.get("name") == name
                and r.get("instrumenttype") == "OPTIDX"
            ]
        return self._nfo_cache

    @staticmethod
    def _parse_expiry(s: str):
        from datetime import datetime as _dt

        for fmt in ("%d%b%Y", "%d%b%y"):
            try:
                return _dt.strptime(s.strip().upper(), fmt).date()
            except ValueError:
                continue
        return None

    def get_option_expiries(self, underlying: str):
        from datetime import date as _date

        today = _date.today()
        exps = sorted({e for r in self._nfo_options(underlying)
                       if (e := self._parse_expiry(r.get("expiry", ""))) is not None})
        return [e for e in exps if e >= today][:6]

    def get_option_quotes(self, underlying, expiry, items):
        smart = self._require_auth()
        want = {(int(strike), t) for strike, t in items}
        tokenmap: dict[str, str] = {}  # token -> "24500PE"
        for r in self._nfo_options(underlying):
            if self._parse_expiry(r.get("expiry", "")) != expiry:
                continue
            try:
                strike = int(round(float(r.get("strike", 0)) / 100.0))
            except (TypeError, ValueError):
                continue
            opt_type = "CE" if r.get("symbol", "").endswith("CE") else "PE"
            if (strike, opt_type) in want:
                tokenmap[str(r["token"])] = f"{strike}{opt_type}"
        out: dict[str, float] = {}
        tokens = list(tokenmap.keys())
        for i in range(0, len(tokens), 50):  # SmartAPI market-data batch limit
            try:
                resp = smart.getMarketData("LTP", {"NFO": tokens[i:i + 50]})
                for row in (resp.get("data", {}) or {}).get("fetched", []):
                    key = tokenmap.get(str(row.get("symbolToken")))
                    if key:
                        out[key] = float(row.get("ltp"))
            except Exception as e:
                log.warning("Angel option LTP batch failed: %s", e)
        return out
