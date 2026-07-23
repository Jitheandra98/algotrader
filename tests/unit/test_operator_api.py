"""FastAPI operator API smoke tests using the TestClient."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from core.schemas import AssetClass, Direction, Fill, Instrument, OrderSide, Position, Signal
from monitoring import HealthMonitor, MetricsRegistry
from monitoring.api import OperatorState, create_app
from risk import KillSwitch, RiskDecision

UTC = timezone.utc


def _spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF)


def _make_state(*, ks: KillSwitch | None = None) -> OperatorState:
    pos = [Position(
        instrument=_spy(),
        quantity=Decimal("10"),
        average_price=Decimal("100"),
        current_price=Decimal("110"),
        unrealized_pnl=Decimal("100"),
    )]
    sig = Signal(
        strategy_id="sma",
        instrument=_spy(),
        direction=Direction.LONG,
        conviction=0.8,
        rationale="t",
    )
    fill = Fill(
        order_id=uuid4(),
        instrument=_spy(),
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        timestamp=datetime.now(tz=UTC),
    )
    decision = RiskDecision(signal=sig, allowed=True, order=None)
    return OperatorState(
        portfolio_provider=lambda: {"equity": 110000.0, "cash": 99000.0, "positions_value": 11000.0},
        positions_provider=lambda: pos,
        signals_provider=lambda n: [sig] * min(n, 3),
        fills_provider=lambda n: [fill] * min(n, 5),
        risk_audit_provider=lambda n: [decision] * min(n, 4),
        strategies_provider=lambda: [{"name": "sma", "lifecycle": "PAPER"}],
        kill_switch=ks,
        health_monitor=None,
        metrics=MetricsRegistry(),
    )


def test_portfolio_endpoint() -> None:
    app = create_app(_make_state())
    with TestClient(app) as c:
        r = c.get("/api/portfolio")
        assert r.status_code == 200
        assert r.json()["equity"] == 110000.0


def test_positions_endpoint_serializes_pydantic() -> None:
    app = create_app(_make_state())
    with TestClient(app) as c:
        r = c.get("/api/positions")
        assert r.status_code == 200
        out = r.json()
        assert len(out) == 1
        assert out[0]["instrument"]["symbol"] == "SPY"


def test_signals_endpoint_honors_limit() -> None:
    app = create_app(_make_state())
    with TestClient(app) as c:
        r = c.get("/api/signals?limit=2")
        assert r.status_code == 200
        assert len(r.json()) == 2


def test_risk_audit_serializes_rejections() -> None:
    app = create_app(_make_state())
    with TestClient(app) as c:
        r = c.get("/api/risk-audit?limit=10")
        assert r.status_code == 200
        out = r.json()
        assert out[0]["allowed"] is True
        assert "signal" in out[0]


def test_metrics_endpoint_returns_prometheus_text() -> None:
    app = create_app(_make_state())
    with TestClient(app) as c:
        r = c.get("/metrics")
        assert r.status_code == 200
        assert b"algotrader_equity_usd" in r.content


def test_kill_switch_trip_and_reset() -> None:
    ks = KillSwitch()
    app = create_app(_make_state(ks=ks))
    with TestClient(app) as c:
        r = c.get("/api/kill-switch")
        assert r.json() == {"tripped": False, "reasons": []}
        c.post("/api/kill-switch/trip?reason=test")
        assert ks.is_tripped
        c.post("/api/kill-switch/reset")
        assert not ks.is_tripped


def test_kill_switch_503_when_not_wired() -> None:
    app = create_app(_make_state(ks=None))
    with TestClient(app) as c:
        r = c.get("/api/kill-switch")
        assert r.status_code == 503


def test_index_serves_ui_html() -> None:
    app = create_app(_make_state())
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "<html" in r.text.lower()
        assert "algotrader" in r.text.lower()
