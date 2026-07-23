"""SimulatedBroker fill matching tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backtest.broker import SimulatedBroker
from backtest.commission import PerTradeCommission, ZeroCommission
from backtest.slippage import PercentSlippage, ZeroSlippage
from core.schemas import (
    AssetClass,
    Bar,
    BarTimeframe,
    Fill,
    Instrument,
    Order,
    OrderSide,
    OrderType,
    TimeInForce,
)

UTC = timezone.utc


def _inst() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF)


def _bar(o, h, l, c) -> Bar:
    return Bar(
        instrument=_inst(),
        timeframe=BarTimeframe.D1,
        timestamp=datetime(2026, 5, 25, 13, 30, tzinfo=UTC),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal("1000"),
    )


def _market_buy(qty: str = "10") -> Order:
    return Order(
        client_order_id="t1",
        instrument=_inst(),
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal(qty),
        strategy_id="test",
        broker="sim",
    )


def test_market_order_fills_at_open_plus_slippage() -> None:
    broker = SimulatedBroker(slippage=PercentSlippage(pct="0.01"))  # +1%
    broker.submit(_market_buy())
    fills = broker.match(_bar(100, 105, 99, 104))
    assert len(fills) == 1
    # 100 * (1 + 0.01) = 101 (adverse for BUY)
    assert fills[0].price == Decimal("101.00")


def test_limit_buy_fills_when_low_below_limit() -> None:
    broker = SimulatedBroker(slippage=ZeroSlippage())
    order = Order(
        client_order_id="lb",
        instrument=_inst(),
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("99"),
        strategy_id="test",
        broker="sim",
    )
    broker.submit(order)
    # Bar low=98 ≤ 99 → filled at limit price.
    fills = broker.match(_bar(100, 105, 98, 102))
    assert len(fills) == 1
    assert fills[0].price == Decimal("99")


def test_limit_buy_does_not_fill_when_low_above_limit() -> None:
    broker = SimulatedBroker()
    order = Order(
        client_order_id="lb2",
        instrument=_inst(),
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("95"),
        strategy_id="test",
        broker="sim",
    )
    broker.submit(order)
    fills = broker.match(_bar(100, 105, 99, 102))
    assert fills == []
    assert len(broker.pending_orders) == 1  # still pending


def test_stop_sell_triggers_when_low_below_stop() -> None:
    broker = SimulatedBroker(slippage=ZeroSlippage())
    order = Order(
        client_order_id="ss",
        instrument=_inst(),
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        quantity=Decimal("10"),
        stop_price=Decimal("95"),
        strategy_id="test",
        broker="sim",
    )
    broker.submit(order)
    fills = broker.match(_bar(100, 102, 94, 98))
    assert len(fills) == 1
    # min(stop=95, open=100) = 95
    assert fills[0].price == Decimal("95")


def test_stop_buy_triggers_when_high_above_stop_with_gap() -> None:
    broker = SimulatedBroker(slippage=ZeroSlippage())
    order = Order(
        client_order_id="sb",
        instrument=_inst(),
        side=OrderSide.BUY,
        order_type=OrderType.STOP,
        quantity=Decimal("10"),
        stop_price=Decimal("105"),
        strategy_id="test",
        broker="sim",
    )
    broker.submit(order)
    # Gap up: open=108 > stop=105. Fill at max(stop, open) = 108.
    fills = broker.match(_bar(108, 110, 107, 109))
    assert len(fills) == 1
    assert fills[0].price == Decimal("108")


def test_commission_applied_to_fill() -> None:
    broker = SimulatedBroker(commission=PerTradeCommission(per_trade="1.50"))
    broker.submit(_market_buy())
    fills = broker.match(_bar(100, 105, 99, 102))
    assert fills[0].commission == Decimal("1.50")


def test_cancel_removes_pending() -> None:
    broker = SimulatedBroker()
    order = broker.submit(_market_buy())
    assert broker.cancel(order.id) is True
    assert broker.pending_orders == []
    # cancelling a fictional id is a no-op (returns False)
    from uuid import uuid4
    assert broker.cancel(uuid4()) is False


def test_bracket_oco_cancels_sibling_on_fill() -> None:
    broker = SimulatedBroker(slippage=ZeroSlippage())
    parent = _market_buy(qty="10")
    parent_a, tp, sl = broker.submit_bracket(
        parent, take_profit_price=Decimal("105"), stop_loss_price=Decimal("95")
    )
    # First bar: parent fills, children activate.
    fills = broker.match(_bar(100, 101, 100, 101))
    assert len(fills) == 1
    # Children should now be active (status NEW).
    pending = {o.id: o for o in broker.pending_orders}
    assert tp is not None and sl is not None
    assert tp.id in pending
    assert sl.id in pending

    # Next bar: low touches stop_loss=95 first. The current logic fills SL and
    # cancels TP.
    fills2 = broker.match(_bar(100, 101, 94, 96))
    assert len(fills2) == 1
    assert fills2[0].price == Decimal("95")  # SL fill at stop_price
    # TP should have been cancelled.
    assert tp.id not in {o.id for o in broker.pending_orders}
