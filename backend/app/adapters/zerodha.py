"""Zerodha Kite Connect adapter (kiteconnect, request-token flow).

Flow: GET /api/broker/status returns the Kite login URL → user logs in on
Zerodha → Zerodha redirects with a request_token → POST /api/broker/authenticate
{"request_token": "..."} exchanges it for an access token.
"""
from __future__ import annotations

import logging
import time

from .base import BrokerAdapter, BrokerError, BrokerStatus, Candle, Instrument
from ..config import env

log = logging.getLogger(__name__)

_INTERVAL_MAP = {"day": "day", "week": "week"}


class ZerodhaAdapter(BrokerAdapter):
    name = "zerodha"

    def __init__(self) -> None:
        self._kite = None
        self._access_token: str | None = env.kite_access_token or None
        self._instruments: dict[str, Instrument] = {}
        self._detail = "Not authenticated"

    def _client(self):
        try:
            from kiteconnect import KiteConnect  # type: ignore
        except ImportError as e:
            raise BrokerError(f"kiteconnect not installed: {e}") from e
        if not env.kite_api_key:
            raise BrokerError("KITE_API_KEY missing in environment (.env)")
        if self._kite is None:
            self._kite = KiteConnect(api_key=env.kite_api_key)
            if self._access_token:
                self._kite.set_access_token(self._access_token)
        return self._kite

    # ------------------------------------------------------------------
    def authenticate(self, request_token: str | None = None, **kwargs) -> BrokerStatus:
        kite = self._client()
        if request_token:
            if not env.kite_api_secret:
                raise BrokerError("KITE_API_SECRET missing in environment (.env)")
            try:
                data = kite.generate_session(request_token, api_secret=env.kite_api_secret)
            except Exception as e:
                raise BrokerError(f"Kite session exchange failed: {e}") from e
            self._access_token = data["access_token"]
            kite.set_access_token(self._access_token)
        if not self._access_token:
            raise BrokerError("Kite requires a request_token (open the login URL first)")
        try:
            kite.profile()  # validate token
            self._detail = "Authenticated"
            self._load_instruments()
        except Exception as e:
            self._access_token = None
            raise BrokerError(f"Kite token invalid/expired: {e}") from e
        return self.status()

    def status(self) -> BrokerStatus:
        login_url = None
        try:
            login_url = self._client().login_url()
        except BrokerError as e:
            return BrokerStatus(name=self.name, authenticated=False, detail=str(e))
        ok = self._access_token is not None
        return BrokerStatus(
            name=self.name,
            authenticated=ok,
            detail=self._detail if ok else "Awaiting request-token login",
            login_url=login_url,
        )

    # ------------------------------------------------------------------
    def _load_instruments(self) -> None:
        kite = self._client()
        try:
            rows = kite.instruments("NSE")
        except Exception as e:
            log.warning("Kite instruments fetch failed: %s", e)
            return
        for r in rows:
            if r.get("instrument_type") == "EQ":
                self._instruments[r["tradingsymbol"]] = Instrument(
                    symbol=r["tradingsymbol"], name=r.get("name", ""),
                    exchange="NSE", token=str(r.get("instrument_token", "")),
                )
        self._instruments["NIFTY 50"] = Instrument(symbol="NIFTY 50", name="NIFTY 50",
                                                   exchange="NSE", token="256265")

    def _token_for(self, symbol: str) -> int:
        inst = self._instruments.get(symbol)
        if not inst or not inst.token:
            raise BrokerError(f"No Kite instrument token for {symbol}")
        return int(inst.token)

    # ------------------------------------------------------------------
    def get_historical_ohlc(self, symbol, interval, from_dt, to_dt) -> list[Candle]:
        kite = self._client()
        if not self._access_token:
            raise BrokerError("Zerodha not authenticated")
        try:
            rows = kite.historical_data(
                self._token_for(symbol), from_dt, to_dt, _INTERVAL_MAP[interval]
            )
        except Exception as e:
            raise BrokerError(f"Kite historical fetch failed for {symbol}: {e}") from e
        time.sleep(0.35)  # 3 req/s historical limit
        return [
            Candle(ts=r["date"].replace(tzinfo=None), open=r["open"], high=r["high"],
                   low=r["low"], close=r["close"], volume=float(r.get("volume", 0)))
            for r in rows
        ]

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        kite = self._client()
        if not self._access_token:
            raise BrokerError("Zerodha not authenticated")
        out: dict[str, float] = {}
        keys = [f"NSE:{s}" for s in symbols]
        # Kite quote API allows up to ~500 instruments per call — chunk anyway
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            try:
                data = kite.ltp(chunk)
                for k, v in data.items():
                    out[k.split(":", 1)[1]] = float(v["last_price"])
            except Exception as e:
                log.warning("Kite LTP batch failed: %s", e)
        return out

    def get_instruments(self) -> list[Instrument]:
        return list(self._instruments.values())

    # --- Options (NFO) --------------------------------------------------------
    def _nfo_options(self, underlying: str) -> list[dict]:
        """Cached NFO option instrument rows for the underlying index."""
        if not getattr(self, "_nfo_cache", None):
            kite = self._client()
            if not self._access_token:
                raise BrokerError("Zerodha not authenticated")
            try:
                self._nfo_cache = kite.instruments("NFO")
            except Exception as e:
                raise BrokerError(f"Kite NFO instruments fetch failed: {e}") from e
        name = underlying.replace(" 50", "").strip()  # "NIFTY 50" → "NIFTY"
        return [r for r in self._nfo_cache
                if r.get("name") == name and r.get("instrument_type") in ("CE", "PE")]

    def get_option_expiries(self, underlying: str):
        from datetime import date as _date

        rows = self._nfo_options(underlying)
        today = _date.today()
        exps = sorted({r["expiry"].date() if hasattr(r["expiry"], "date") else r["expiry"]
                       for r in rows})
        return [e for e in exps if e >= today][:6]

    def get_option_quotes(self, underlying, expiry, items):
        kite = self._client()
        rows = self._nfo_options(underlying)
        want = {(int(strike), t) for strike, t in items}
        keymap: dict[str, str] = {}  # "NFO:tradingsymbol" -> "24500PE"
        for r in rows:
            exp = r["expiry"].date() if hasattr(r["expiry"], "date") else r["expiry"]
            if exp != expiry:
                continue
            k = (int(float(r["strike"])), r["instrument_type"])
            if k in want:
                keymap[f"NFO:{r['tradingsymbol']}"] = f"{k[0]}{k[1]}"
        out: dict[str, float] = {}
        keys = list(keymap.keys())
        for i in range(0, len(keys), 400):
            try:
                data = kite.ltp(keys[i:i + 400])
                for full, v in data.items():
                    out[keymap[full]] = float(v["last_price"])
            except Exception as e:
                log.warning("Kite option LTP batch failed: %s", e)
        return out
