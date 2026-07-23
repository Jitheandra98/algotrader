"""Fill quality tracker tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from core.schemas import (
    AssetClass,
    Fill,
    Instrument,
    Order,
    OrderSide,
    OrderType,
)
from execution.quality import FillQualityTracker, reference_price_for

UTC = timezone.utc


def _inst() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF)


def _order(side=OrderSide.BUY, otype=OrderType.MARKET, qty="100", limit=None, stop=None) -> Order:
    return Order(
        client_order_id=f"co-{uuid4().hex[:6]}",
        instrument=_inst(),
        side=side,
        order_type=otype,
        quantity=Decimal(qty),
        limit_price=Decimal(limit) if limit else None,
        stop_price=Decimal(stop) if stop else None,
        strategy_id="s1",
        broker="mock",
    )


def _fill(order, *, price, when) -> Fill:
    return Fill(
        order_id=order.id,
        instrument=order.instrument,
        side=order.side,
        quantity=order.quantity,
        price=Decimal(str(price)),
        timestamp=when,
    )


def test_reference_price_for_market_buy_uses_ask() -> None:
    o = _order()
    ref = reference_price_for(o, bid=Decimal("99.99"), ask=Decimal("100.01"), mid=Decimal("100"))
    assert ref == Decimal("100.01")


def test_reference_price_for_market_sell_uses_bid() -> None:
    o = _order(side=OrderSide.SELL)
    ref = reference_price_for(o, bid=Decimal("99.99"), ask=Decimal("100.01"), mid=Decimal("100"))
    assert ref == Decimal("99.99")


def test_reference_price_for_limit_uses_limit() -> None:
    o = _order(otype=OrderType.LIMIT, limit="99")
    ref = reference_price_for(o, bid=Decimal("99.99"), ask=Decimal("100.01"), mid=Decimal("100"))
    assert ref == Decimal("99")


def test_quality_tracker_records_slippage_and_latency() -> None:
    t = FillQualityTracker()
    o = _order()
    submit_time = datetime.now(tz=UTC)
    t.on_submit(o, reference_price=Decimal("100"))
    fill_time = submit_time + timedelta(milliseconds=150)
    rec = t.on_fill(_fill(o, price="100.05", when=fill_time), strategy_id="s1", venue="alpaca")
    # Slippage on BUY at 100.05 vs ref 100 = +0.05 per unit (adverse)
    assert rec.slippage_per_unit == Decimal("0.05")
    assert rec.total_slippage == Decimal("5")  # 0.05 × 100
    assert 100 <= rec.fill_latency_ms <= 250


def test_quality_tracker_stats_aggregates() -> None:
    t = FillQualityTracker()
    for i in range(10):
        o = _order(qty="10")
        t.on_submit(o, reference_price=Decimal("100"))
        rec = t.on_fill(
            _fill(o, price=Decimal("100") + Decimal(str(i)) / 100, when=datetime.now(tz=UTC)),
            strategy_id="s1",
        )
        del rec
    stats = t.stats(strategy_id="s1")
    assert stats is not None
    assert stats.count == 10
    # Slippage rises linearly 0.00..0.09; mean ≈ 0.045
    assert 0.04 <= stats.avg_slippage_per_unit <= 0.05


def test_quality_tracker_returns_none_for_no_data() -> None:
    t = FillQualityTracker()
    assert t.stats() is None
