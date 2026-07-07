"""End-to-end options flow against the mock broker + temp SQLite DB."""
import os
import tempfile

import pytest

_tmpdir = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmpdir}/test_opt.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        # ensure NIFTY history exists so the regime module works
        c.put("/api/settings", json={"universe": "NIFTY50"})
        yield c


def test_generate_option_signal(client):
    r = client.post("/api/options/signal/generate", json={"strategy": "auto"})
    assert r.status_code == 200, r.text
    sig = r.json()["signal"]
    assert sig["underlying"] == "NIFTY 50"
    if not sig["entry_ok"]:
        # legitimate block (e.g. DTE window) — reason must be explicit
        assert sig["entry_block_reason"]
        pytest.skip(f"entry blocked: {sig['entry_block_reason']}")
    # deterministic economics
    assert sig["credit"] > 0
    assert sig["max_loss"] > 0
    assert sig["margin_per_lot"] == pytest.approx(sig["max_loss"] * 75, rel=0.01)
    assert sig["roc_pct"] == pytest.approx(sig["credit"] / sig["max_loss"] * 100, rel=0.01)
    assert 0 < sig["pop_pct"] < 100
    assert sig["expected_move"] > 0
    assert len(sig["legs"]) in (2, 4)
    sells = [l for l in sig["legs"] if l["side"] == "sell"]
    buys = [l for l in sig["legs"] if l["side"] == "buy"]
    assert len(sells) == len(buys)  # every short is hedged — defined risk only
    assert sig["reasons"]
    assert sig["chain_snapshot"]


def test_forced_iron_condor(client):
    r = client.post("/api/options/signal/generate", json={"strategy": "iron_condor"})
    sig = r.json()["signal"]
    if sig["entry_ok"]:
        assert sig["strategy"] == "iron_condor"
        assert len(sig["legs"]) == 4


def test_option_paper_lifecycle(client):
    r = client.post("/api/options/signal/generate", json={"strategy": "iron_condor"})
    sig = r.json()["signal"]
    if not sig["entry_ok"]:
        pytest.skip(f"entry blocked: {sig['entry_block_reason']}")

    r = client.post("/api/options/paper/trades", json={"signal_id": sig["id"], "lots": 2})
    assert r.status_code == 200, r.text
    trade = r.json()
    assert trade["status"] == "open"
    assert trade["net_credit"] > 0
    assert trade["lots"] == 2 and trade["lot_size"] == 75
    # every leg filled at a live quote
    assert all(l["entry_price"] > 0 for l in trade["legs"])

    pos = client.get("/api/options/paper/positions").json()["positions"]
    mine = next(p for p in pos if p["id"] == trade["id"])
    assert mine["cost_to_close"] is not None       # live MTM from leg quotes
    assert mine["unrealized_pnl"] is not None
    assert all(l["ltp"] is not None for l in mine["legs"])

    r = client.post(f"/api/options/paper/trades/{trade['id']}/exit")
    assert r.status_code == 200, r.text
    closed = r.json()
    assert closed["status"] == "closed"
    assert closed["exit_reason"] == "Manual Exit"
    assert closed["pnl"] is not None
    assert closed["costs"] > 0                     # brokerage + premium slippage
    assert closed["return_on_margin_pct"] is not None

    perf = client.get("/api/options/paper/performance").json()
    assert perf["overall"]["trades"] >= 1
    assert perf["weekly_target_pct"] == 2.0
    assert perf["weeks"]                            # bucketed by ISO week


def test_expiry_settlement_math():
    from app.options.paper import _intrinsic_quotes, _net_debit

    class T:
        legs = [
            {"side": "sell", "opt_type": "PE", "strike": 23800, "entry_price": 60.0},
            {"side": "buy", "opt_type": "PE", "strike": 23600, "entry_price": 30.0},
        ]

    # spot finishes above both strikes → both worthless → close for 0
    q = _intrinsic_quotes(T, 24500.0)
    assert _net_debit(T.legs, q) == 0.0
    # spot between strikes → short put ITM 100, hedge worthless → debit 100
    q = _intrinsic_quotes(T, 23700.0)
    assert _net_debit(T.legs, q) == pytest.approx(100.0)
    # crash below hedge → debit capped at width 200
    q = _intrinsic_quotes(T, 23000.0)
    assert _net_debit(T.legs, q) == pytest.approx(200.0)
