"""Alpaca broker codec tests — verify REST request shape + trade_updates parsing.

We can't actually hit Alpaca in unit tests; these tests cover the codec layer.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from core.schemas import (
    AssetClass,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from execution import AlpacaBroker
from execution.base import ExecutionEvent


@pytest.fixture
def broker(monkeypatch):
    """Construct an AlpacaBroker without hitting the network. Patch the secret
    provider to deterministic values."""
    from core.secrets import SecretProvider

    class StubSecrets(SecretProvider):
        def get(self, k):
            return "stub-key" if "key" in k else "stub-secret"

        def get_optional(self, k):
            return self.get(k)

    return AlpacaBroker(live=False, secret_provider=StubSecrets())


def _order(otype=OrderType.MARKET, side=OrderSide.BUY, qty="10", limit=None, stop=None, tif=TimeInForce.DAY) -> Order:
    return Order(
        client_order_id=f"t-{uuid4().hex[:6]}",
        instrument=Instrument(symbol="SPY", asset_class=AssetClass.ETF),
        side=side,
        order_type=otype,
        quantity=Decimal(qty),
        limit_price=Decimal(limit) if limit else None,
        stop_price=Decimal(stop) if stop else None,
        time_in_force=tif,
        strategy_id="t",
        broker="alpaca",
    )


def test_order_to_alpaca_market_buy(broker):
    body = broker._order_to_alpaca(_order())
    assert body["symbol"] == "SPY"
    assert body["side"] == "buy"
    assert body["type"] == "market"
    assert body["qty"] == "10"
    assert body["time_in_force"] == "day"


def test_order_to_alpaca_limit_sell(broker):
    body = broker._order_to_alpaca(_order(otype=OrderType.LIMIT, side=OrderSide.SELL, limit="100"))
    assert body["side"] == "sell"
    assert body["type"] == "limit"
    assert body["limit_price"] == "100"


def test_order_to_alpaca_moc_maps_to_market_cls(broker):
    body = broker._order_to_alpaca(_order(otype=OrderType.MOC))
    assert body["type"] == "market"
    assert body["time_in_force"] == "cls"


def test_order_to_alpaca_loc_maps_to_limit_opg(broker):
    body = broker._order_to_alpaca(_order(otype=OrderType.LOC, limit="100"))
    assert body["type"] == "limit"
    assert body["time_in_force"] == "opg"


def test_alpaca_to_order_round_trip(broker):
    incoming = {
        "id": "00000000-0000-0000-0000-000000000001",
        "client_order_id": "t-abc",
        "symbol": "SPY",
        "asset_class": "us_equity",
        "side": "buy",
        "type": "market",
        "qty": "10",
        "status": "new",
    }
    o = broker._alpaca_to_order(incoming)
    assert o.instrument.symbol == "SPY"
    assert o.side is OrderSide.BUY
    assert o.quantity == Decimal("10")
    assert o.status is OrderStatus.NEW


def test_trade_update_fill_to_execution_report(broker):
    data = {
        "event": "fill",
        "timestamp": "2026-05-25T13:30:00.123Z",
        "qty": "10",
        "price": "555.10",
        "execution_id": "exec-1",
        "order": {
            "id": "00000000-0000-0000-0000-000000000002",
            "symbol": "SPY",
            "asset_class": "us_equity",
            "side": "buy",
            "status": "filled",
            "qty": "10",
            "filled_qty": "10",
        },
    }
    report = broker._trade_update_to_report(data)
    assert report is not None
    assert report.event is ExecutionEvent.FILL
    assert report.fill is not None
    assert report.fill.price == Decimal("555.10")
    assert report.fill.quantity == Decimal("10")


def test_trade_update_reject(broker):
    data = {
        "event": "rejected",
        "timestamp": "2026-05-25T13:30:00Z",
        "reason": "insufficient buying power",
        "order": {
            "id": "00000000-0000-0000-0000-000000000003",
            "symbol": "SPY",
            "asset_class": "us_equity",
            "side": "buy",
            "status": "rejected",
            "qty": "10",
            "filled_qty": "0",
        },
    }
    report = broker._trade_update_to_report(data)
    assert report is not None
    assert report.event is ExecutionEvent.REJECT
    assert report.reject_reason == "insufficient buying power"
