"""Smart order router + slicer tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from core.schemas import (
    AssetClass,
    Fill,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from execution import (
    BrokerAdapter,
    ChildOrder,
    IcebergSlicer,
    MarketView,
    SimpleSplitSlicer,
    SmartOrderRouter,
    TwapSlicer,
)
from execution.base import AccountInfo, ExecutionReport

UTC = timezone.utc


def _inst(symbol: str = "SPY") -> Instrument:
    return Instrument(symbol=symbol, asset_class=AssetClass.ETF)


def _order(qty="100", side=OrderSide.BUY, order_type=OrderType.MARKET, limit=None) -> Order:
    return Order(
        client_order_id=f"t-{qty}",
        instrument=_inst(),
        side=side,
        order_type=order_type,
        quantity=Decimal(qty),
        limit_price=Decimal(limit) if limit else None,
        strategy_id="s1",
        broker="mock",
    )


class MockBroker(BrokerAdapter):
    name = "mock"

    def __init__(self) -> None:
        self.submitted: list[Order] = []

    async def connect(self) -> None: pass
    async def close(self) -> None: pass

    async def submit(self, order: Order) -> Order:
        self.submitted.append(order)
        return order.model_copy(update={"status": OrderStatus.NEW, "broker_order_id": f"b-{len(self.submitted)}"})

    async def cancel(self, order_id: UUID) -> bool: return True
    async def get_order(self, order_id: UUID) -> Order | None: return None
    async def positions(self) -> list[Position]: return []
    async def account(self) -> AccountInfo:
        return AccountInfo(cash=Decimal("100000"), equity=Decimal("100000"), buying_power=Decimal("100000"), is_margin_account=False)
    async def stream_order_updates(self) -> AsyncIterator[ExecutionReport]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]


# ---- slicers ----


def test_simple_split_distributes_qty() -> None:
    s = SimpleSplitSlicer(n=4)
    parent = _order(qty="100")
    plan = s.plan(parent, now=datetime(2026, 5, 25, tzinfo=UTC))
    assert len(plan) == 4
    assert sum(c.order.quantity for c in plan) == Decimal("100")
    # Each child carries the parent's id linkage.
    assert all(c.order.parent_order_id == parent.id or c.order.id == parent.id for c in plan)


def test_simple_split_collapses_small_orders() -> None:
    s = SimpleSplitSlicer(n=10, min_child_qty="50")
    plan = s.plan(_order(qty="100"), now=datetime(2026, 5, 25, tzinfo=UTC))
    assert len(plan) == 1  # per-child below min → submit as single


def test_twap_spaces_children_in_time() -> None:
    s = TwapSlicer(n=3, interval=timedelta(seconds=5))
    base = datetime(2026, 5, 25, 14, 0, tzinfo=UTC)
    plan = s.plan(_order(qty="300"), now=base)
    assert [c.release_at for c in plan] == [base, base + timedelta(seconds=5), base + timedelta(seconds=10)]


def test_iceberg_initial_slice_only() -> None:
    s = IcebergSlicer(visible_qty="50")
    plan = s.plan(_order(qty="500"), now=datetime(2026, 5, 25, tzinfo=UTC))
    assert len(plan) == 1
    assert plan[0].order.quantity == Decimal("50")


# ---- router ----


@pytest.mark.asyncio
async def test_router_passthrough_when_quote_clean() -> None:
    broker = MockBroker()
    router = SmartOrderRouter(broker, slice_above_qty="1000")
    router.market_view.update_quote(_inst(), bid=Decimal("99.99"), ask=Decimal("100.01"), bid_size=Decimal("1000"), ask_size=Decimal("1000"))
    decision = await router.route(_order(qty="10"))
    assert decision.reason == "passthrough"
    assert len(broker.submitted) == 1
    assert broker.submitted[0].quantity == Decimal("10")


@pytest.mark.asyncio
async def test_router_downgrades_market_to_limit_on_wide_spread() -> None:
    broker = MockBroker()
    router = SmartOrderRouter(broker, max_spread_bps="10", on_wide_spread="limit_at_mid")
    # Bid 99 / Ask 101 — 200 bps spread on mid 100.
    router.market_view.update_quote(_inst(), bid=Decimal("99"), ask=Decimal("101"), bid_size=Decimal("100"), ask_size=Decimal("100"))
    decision = await router.route(_order(qty="10"))
    assert decision.transformed
    submitted = broker.submitted[0]
    assert submitted.order_type is OrderType.LIMIT
    assert submitted.limit_price == Decimal("100")


@pytest.mark.asyncio
async def test_router_skips_on_wide_spread() -> None:
    broker = MockBroker()
    router = SmartOrderRouter(broker, max_spread_bps="10", on_wide_spread="skip")
    router.market_view.update_quote(_inst(), bid=Decimal("99"), ask=Decimal("101"))
    decision = await router.route(_order(qty="10"))
    assert decision.reason == "wide_spread_skipped"
    assert broker.submitted == []


@pytest.mark.asyncio
async def test_router_slices_large_orders() -> None:
    broker = MockBroker()
    slicer = SimpleSplitSlicer(n=4)
    router = SmartOrderRouter(broker, slicer=slicer, slice_above_qty="10")
    router.market_view.update_quote(_inst(), bid=Decimal("99.99"), ask=Decimal("100.01"), bid_size=Decimal("10000"), ask_size=Decimal("10000"))
    decision = await router.route(_order(qty="100"))
    assert "sliced" in decision.reason
    assert len(broker.submitted) == 4
    assert sum(o.quantity for o in broker.submitted) == Decimal("100")


@pytest.mark.asyncio
async def test_router_slices_when_size_exceeds_top_of_book_depth() -> None:
    broker = MockBroker()
    slicer = SimpleSplitSlicer(n=2)
    router = SmartOrderRouter(broker, slicer=slicer, slice_above_qty="10000", max_pct_of_top=0.5)
    router.market_view.update_quote(_inst(), bid=Decimal("99.99"), ask=Decimal("100.01"), bid_size=Decimal("50"), ask_size=Decimal("50"))
    decision = await router.route(_order(qty="100", side=OrderSide.BUY))
    # qty 100 > 0.5 * 50 = 25 → slice.
    assert "sliced" in decision.reason
    assert len(broker.submitted) == 2


@pytest.mark.asyncio
async def test_router_handles_no_quote_gracefully() -> None:
    broker = MockBroker()
    router = SmartOrderRouter(broker, slice_above_qty="1000")
    decision = await router.route(_order(qty="10"))
    assert decision.reason == "passthrough"
    assert len(broker.submitted) == 1
