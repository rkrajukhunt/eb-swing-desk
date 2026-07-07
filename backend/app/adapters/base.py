"""Pluggable broker adapter interface.

All market-data access goes through this ABC so the rest of the system is
broker-agnostic. Implementations: AngelOneAdapter, ZerodhaAdapter, MockAdapter.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Instrument:
    symbol: str
    name: str = ""
    exchange: str = "NSE"
    token: str = ""
    lot_size: int = 1


@dataclass
class BrokerStatus:
    name: str
    authenticated: bool
    detail: str = ""
    login_url: str | None = None  # for request-token flows (Kite)
    extra: dict = field(default_factory=dict)


class BrokerError(Exception):
    """Raised on auth failures / API errors — callers must handle gracefully."""


class BrokerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def authenticate(self, **kwargs) -> BrokerStatus:
        """Establish a session. May raise BrokerError."""

    @abstractmethod
    def status(self) -> BrokerStatus:
        """Current auth status without side effects."""

    @abstractmethod
    def get_historical_ohlc(
        self, symbol: str, interval: str, from_dt: datetime, to_dt: datetime
    ) -> list[Candle]:
        """interval: 'day' | 'week'. Returns candles sorted ascending by ts."""

    @abstractmethod
    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        """Batched last-traded-price lookup. Missing symbols are omitted."""

    @abstractmethod
    def get_instruments(self) -> list[Instrument]:
        """Tradable instrument master (used to validate the universe)."""
