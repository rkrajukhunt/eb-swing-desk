"""End-to-end smoke tests against a temp SQLite DB + yahoo adapter."""
import os
import tempfile

import pytest

_tmpdir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_and_meta(client):
    assert client.get("/api/health").json()["ok"]
    meta = client.get("/api/meta").json()
    assert "trend_pullback" in meta["strategies"]
    assert "not investment advice" in meta["disclaimer"].lower()


def test_settings_roundtrip(client):
    s = client.get("/api/settings").json()
    assert s["active_broker"] == "yahoo"
    upd = client.put("/api/settings", json={"universe": "NIFTY50", "risk_pct_per_trade": 2.0,
                                            "bogus_key": 1}).json()
    assert upd["universe"] == "NIFTY50"
    assert upd["risk_pct_per_trade"] == 2.0
    assert "bogus_key" not in upd


def test_scan_and_signals(client):
    r = client.post("/api/scan/run", json={"strategy": "all", "refresh": True})
    assert r.status_code == 200, r.text
    latest = client.get("/api/scan/latest").json()
    assert latest["run"] is not None
    assert latest["run"]["market_regime"] in ("bullish", "neutral", "bearish", "unknown")
    for sig in latest["signals"]:
        risk = sig["entry"] - sig["stop_loss"]
        assert risk > 0
        assert sig["target_2r"] == pytest.approx(sig["entry"] + 2 * risk, abs=0.05)
        assert sig["target_3r"] == pytest.approx(sig["entry"] + 3 * risk, abs=0.05)
        assert sig["risk_reward"] >= 1.5
        assert sig["composite_score"] >= 0
        assert sig["reasons"], "every signal must carry its formula audit trail"


def test_regime_endpoint(client):
    r = client.get("/api/regime").json()
    assert r["regime"] in ("bullish", "neutral", "bearish", "unknown")


def test_paper_trade_lifecycle(client):
    latest = client.get("/api/scan/latest").json()
    if latest["signals"]:
        sig = latest["signals"][0]
        symbol, sl, tgt = sig["symbol"], sig["stop_loss"], sig["target_2r"]
        signal_id = sig["id"]
    else:
        symbol, sl, tgt, signal_id = "TCS", 1.0, 99999.0, None

    r = client.post("/api/paper/trades", json={
        "symbol": symbol, "qty": 10, "stop_loss": sl, "target": tgt,
        "strategy": "breakout", "signal_id": signal_id,
    })
    assert r.status_code == 200, r.text
    trade = r.json()
    assert trade["status"] == "open"
    assert trade["entry_price"] > 0  # live LTP at click

    pos = client.get("/api/paper/positions").json()["positions"]
    assert any(p["id"] == trade["id"] for p in pos)
    mine = next(p for p in pos if p["id"] == trade["id"])
    assert mine["ltp"] is not None and mine["unrealized_pnl"] is not None

    # invalid SL rejected
    bad = client.post("/api/paper/trades", json={
        "symbol": symbol, "qty": 10, "stop_loss": 10_000_000.0, "target": 20_000_000.0})
    assert bad.status_code == 400

    r = client.post(f"/api/paper/trades/{trade['id']}/exit")
    assert r.status_code == 200
    closed = r.json()
    assert closed["status"] == "closed"
    assert closed["exit_reason"] == "Manual Exit"
    assert closed["pnl"] is not None and closed["costs"] > 0

    perf = client.get("/api/paper/performance").json()
    assert perf["overall"]["trades"] >= 1


def test_chart_endpoint(client):
    latest = client.get("/api/scan/latest").json()
    symbol = latest["signals"][0]["symbol"] if latest["signals"] else "TCS"
    chart = client.get(f"/api/symbols/{symbol}/chart").json()
    assert len(chart["candles"]) > 50
    assert len(chart["emas"]["ema20"]) == len(chart["candles"])


def test_watchlist_crud(client):
    client.post("/api/watchlist", json={"symbol": "TCS", "note": "watch"})
    items = client.get("/api/watchlist").json()["items"]
    assert any(i["symbol"] == "TCS" for i in items)
    dup = client.post("/api/watchlist", json={"symbol": "TCS"})
    assert dup.status_code == 409
    item_id = next(i["id"] for i in items if i["symbol"] == "TCS")
    client.delete(f"/api/watchlist/{item_id}")
    items = client.get("/api/watchlist").json()["items"]
    assert not any(i["symbol"] == "TCS" for i in items)


def test_backtest_smoke(client):
    # small universe already refreshed by the scan test
    r = client.post("/api/backtest/run", json={"strategy": "all", "years": 2})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["symbols_tested"] > 0
    assert "overall" in data and "by_strategy" in data
    if data["overall"]["trades"]:
        assert data["overall"]["equity_curve"]
        for t in data["trades"]:
            assert t["reason"] in ("Stopped Out", "Target Hit", "Time Stop")
