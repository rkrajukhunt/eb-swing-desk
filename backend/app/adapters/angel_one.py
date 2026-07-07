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


class AngelOneAdapter(BrokerAdapter):
    name = "angel_one"

    def __init__(self) -> None:
        self._smart = None
        self._instruments: dict[str, Instrument] = {}
        self._detail = "Not authenticated"

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
            self._detail = "Authenticated via TOTP"
            self._load_instruments()
            return self.status()
        except BrokerError:
            raise
        except Exception as e:  # network / SDK errors
            raise BrokerError(f"Angel One auth error: {e}") from e

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
        try:
            resp = smart.getCandleData(params)
        except Exception as e:
            raise BrokerError(f"Angel One candle fetch failed for {symbol}: {e}") from e
        if not resp or not resp.get("status"):
            raise BrokerError(f"Angel One candle fetch failed for {symbol}: {resp and resp.get('message')}")
        candles = [
            Candle(
                ts=datetime.fromisoformat(row[0][:19]),
                open=float(row[1]), high=float(row[2]), low=float(row[3]),
                close=float(row[4]), volume=float(row[5]),
            )
            for row in (resp.get("data") or [])
        ]
        if interval == "week":
            from .mock import _resample_weekly

            candles = _resample_weekly(candles)
        time.sleep(0.35)  # respect ~3 req/s historical rate limit
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
