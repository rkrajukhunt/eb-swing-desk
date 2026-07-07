from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class OhlcCandle(Base):
    """Cached historical OHLCV — avoids hammering broker rate limits."""

    __tablename__ = "ohlc_candles"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "ts", name="uq_candle"),
        Index("ix_candle_lookup", "symbol", "interval", "ts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    exchange: Mapped[str] = mapped_column(String(8), default="NSE")
    interval: Mapped[str] = mapped_column(String(8))  # "day" | "week"
    ts: Mapped[datetime] = mapped_column(DateTime)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    universe: Mapped[str] = mapped_column(String(32))
    strategy_filter: Mapped[str] = mapped_column(String(32), default="all")
    market_regime: Mapped[str] = mapped_column(String(16), default="unknown")
    regime_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    scanned_count: Mapped[int] = mapped_column(Integer, default=0)
    signal_count: Mapped[int] = mapped_column(Integer, default=0)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    settings_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signal_run", "scan_run_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(32))
    strategy: Mapped[str] = mapped_column(String(32))
    composite_score: Mapped[float] = mapped_column(Float)
    sub_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    penalties: Mapped[list] = mapped_column(JSON, default=list)
    # Deterministic price levels — the ONLY source of truth for the UI / paper trades
    entry: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    target_2r: Mapped[float] = mapped_column(Float)
    target_3r: Mapped[float] = mapped_column(Float)
    risk_reward: Mapped[float] = mapped_column(Float)
    pct_risk: Mapped[float] = mapped_column(Float)
    suggested_qty: Mapped[int] = mapped_column(Integer, default=0)
    indicators: Mapped[dict] = mapped_column(JSON, default=dict)
    reasons: Mapped[list] = mapped_column(JSON, default=list)  # formula audit trail
    # LLM layer (additive only — never overrides the numbers above)
    llm_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_conviction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    llm_rationale: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    llm_conflicts: Mapped[list] = mapped_column(JSON, default=list)
    llm_regime_note: Mapped[str | None] = mapped_column(String(512), nullable=True)


class PaperTrade(Base):
    __tablename__ = "paper_trades"
    __table_args__ = (Index("ix_paper_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    strategy: Mapped[str] = mapped_column(String(32), default="manual")
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    broker_source: Mapped[str] = mapped_column(String(16), default="mock")
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    qty: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)   # live LTP at click
    stop_loss: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    initial_risk: Mapped[float] = mapped_column(Float)  # entry - initial SL (for R multiples)
    trailing: Mapped[bool] = mapped_column(Boolean, default=False)
    trail_atr: Mapped[float] = mapped_column(Float, default=0.0)   # ATR at entry, for trailing
    highest_close: Mapped[float] = mapped_column(Float, default=0.0)
    entry_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Stopped Out | Target Hit | Manual Exit | Trail Stop
    costs: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)   # net of costs
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    holding_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("symbol", name="uq_watch_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(256), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    """Single-row JSON blob holding user-tunable settings (merged over defaults)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
