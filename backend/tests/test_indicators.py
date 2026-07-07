import numpy as np
import pandas as pd
import pytest

from app.engine.indicators import (
    adx,
    atr,
    bollinger,
    compute_indicators,
    ema,
    macd,
    rsi,
)


@pytest.fixture
def trending_df():
    """Deterministic uptrending OHLCV frame."""
    rng = np.random.default_rng(42)
    n = 400
    closes = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.01, n)))
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame({
        "open": closes * (1 + rng.normal(0, 0.002, n)),
        "close": closes,
        "volume": rng.integers(100_000, 1_000_000, n).astype(float),
    }, index=idx)
    df["high"] = df[["open", "close"]].max(axis=1) * 1.005
    df["low"] = df[["open", "close"]].min(axis=1) * 0.995
    return df


def test_ema_converges_to_constant():
    s = pd.Series([100.0] * 300)
    assert abs(ema(s, 20).iloc[-1] - 100.0) < 1e-9


def test_rsi_bounds_and_direction(trending_df):
    r = rsi(trending_df["close"])
    assert ((r.dropna() >= 0) & (r.dropna() <= 100)).all()
    # pure up-moves → RSI == 100
    up = pd.Series(np.arange(1.0, 101.0))
    assert rsi(up).iloc[-1] > 99.0


def test_rsi_wilder_reference():
    # Classic Wilder example series should give RSI ~70 region after 14 gains-heavy bars
    prices = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                        45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
                        46.03, 46.41, 46.22, 45.64])
    r = rsi(prices, 14)
    assert 50 < r.iloc[14] < 80


def test_atr_positive(trending_df):
    a = atr(trending_df["high"], trending_df["low"], trending_df["close"])
    assert (a.iloc[20:] > 0).all()


def test_adx_bounds(trending_df):
    a, p, m = adx(trending_df["high"], trending_df["low"], trending_df["close"])
    assert ((a.iloc[30:] >= 0) & (a.iloc[30:] <= 100)).all()


def test_bollinger_ordering(trending_df):
    up, mid, lo = bollinger(trending_df["close"])
    tail = slice(25, None)
    assert (up.iloc[tail] >= mid.iloc[tail]).all()
    assert (mid.iloc[tail] >= lo.iloc[tail]).all()


def test_macd_shapes(trending_df):
    line, sig, hist = macd(trending_df["close"])
    assert np.allclose((line - sig).iloc[50:], hist.iloc[50:])


def test_compute_indicators_no_lookahead_weekly(trending_df):
    from app.services.market_data import to_weekly

    weekly = to_weekly(trending_df)
    ind = compute_indicators(trending_df, weekly)
    # weekly_up on the final bar must be derivable from completed weeks only:
    # recomputing with the last (incomplete) week dropped must not change
    # earlier daily values
    weekly_trunc = weekly.iloc[:-1]
    ind2 = compute_indicators(trending_df, weekly_trunc)
    cutoff = weekly_trunc.index[-1]
    a = ind.loc[:cutoff, "weekly_up"].iloc[:-5]
    b = ind2.loc[:cutoff, "weekly_up"].iloc[:-5]
    assert (a == b).all()


def test_compute_indicators_columns(trending_df):
    from app.services.market_data import to_weekly

    ind = compute_indicators(trending_df, to_weekly(trending_df))
    for col in ["ema20", "ema50", "ema200", "rsi14", "macd_hist", "adx14",
                "atr14", "bb_upper", "bb_lower", "vol_ratio", "high_20",
                "swing_low", "high_52w", "dist_52w_high_pct", "rel_strength_55d",
                "weekly_up", "rsi_divergence", "turnover_avg20"]:
        assert col in ind.columns, col
