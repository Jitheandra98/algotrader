"""Broker chaos: connection drop, order submission failure, repeated rejects."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from core.schemas import (
    AssetClass,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from execution.base import AccountInfo, BrokerAdapter, ExecutionReport
from monitoring import (
    BrokerConnectionCheck,
    HealthCircuitBreaker,
    HealthMonitor,
    OrderRejectionRateCheck,
)
from risk import KillSwitch

UTC = timezone.utc


class FlakyBroker(BrokerAdapter):
    name = "flaky"

    def __init__(self) -> None:
        self.connected = True
        self.submitted: list[Order] = []
        self.fail_on_submit = False
        self.connect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def submit(self, order: Order) -> Order:
        if self.fail_on_submit:
            raise ConnectionError("broker connection dropped")
        self.submitted.append(order)
        return order.model_copy(update={"status": OrderStatus.NEW})

    async def cancel(self, order_id: UUID) -> bool:
        return True

    async def get_order(self, order_id: UUID) -> Order | None:
        return None

    async def positions(self) -> list[Position]:
        return []

    async def account(self) -> AccountInfo:
        return AccountInfo(
            cash=Decimal("100000"),
            equity=Decimal("100000"),
            buying_power=Decimal("100000"),
            is_margin_account=False,
        )

    async def stream_order_updates(self) -> AsyncIterator[ExecutionReport]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


@pytest.mark.asyncio
async def test_broker_connection_drop_trips_circuit_breaker() -> None:
    broker = FlakyBroker()
    ks = KillSwitch()
    monitor = HealthMonitor([BrokerConnectionCheck(is_connected=lambda: broker.connected)])
    breaker = HealthCircuitBreaker(kill_switch=ks, monitor=monitor, trip_after=2)

    breaker.evaluate()
    assert not ks.is_tripped
    broker.connected = False
    breaker.evaluate()
    breaker.evaluate()
    assert ks.is_tripped


def test_order_rejection_rate_breaker_trips_on_burst() -> None:
    submitted = {"n": 100}
    rejected = {"n": 50}  # 50% rejection rate
    ks = KillSwitch()
    monitor = HealthMonitor([
        OrderRejectionRateCheck(
            get_submitted=lambda: submitted["n"],
            get_rejected=lambda: rejected["n"],
            max_rate=0.10,
            min_sample=10,
        ),
    ])
    breaker = HealthCircuitBreaker(kill_switch=ks, monitor=monitor, trip_after=2)
    breaker.evaluate()
    breaker.evaluate()
    assert ks.is_tripped


@pytest.mark.asyncio
async def test_submit_failure_does_not_crash_caller() -> None:
    """A broker that raises on submit should propagate the error cleanly —
    not corrupt internal state. The caller is expected to handle the exception
    and retry / log / alert."""
    broker = FlakyBroker()
    broker.fail_on_submit = True
    order = Order(
        client_order_id="t1",
        instrument=Instrument(symbol="SPY", asset_class=AssetClass.ETF),
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        strategy_id="s",
        broker="flaky",
    )
    with pytest.raises(ConnectionError):
        await broker.submit(order)
    # Nothing leaked into submitted list.
    assert broker.submitted == []
