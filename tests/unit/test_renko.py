"""Renko aggregator tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from core.schemas import AssetClass, BarTimeframe, Instrument, Tick
from data.aggregators import RenkoAggregator

UTC = timezone.utc


def _spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")


def _trade(ts: datetime, price: str, size: str = "100") -> Tick:
    return Tick(instrument=_spy(), timestamp=ts, last_price=Decimal(price), last_size=Decimal(size))


def test_first_tick_seeds_baseline_no_emit() -> None:
    agg = RenkoAggregator(brick_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    out = agg.on_tick(_trade(ts, "100"))
    assert out == []


def test_one_brick_up() -> None:
    agg = RenkoAggregator(brick_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    agg.on_tick(_trade(ts, "100"))
    out = agg.on_tick(_trade(ts, "101"))
    assert len(out) == 1
    b = out[0]
    assert b.open == Decimal("100")
    assert b.close == Decimal("101")
    assert b.high == Decimal("101")
    assert b.low == Decimal("100")


def test_multiple_bricks_one_tick() -> None:
    """A single tick that jumps three brick widths emits three bricks."""
    agg = RenkoAggregator(brick_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    agg.on_tick(_trade(ts, "100"))
    out = agg.on_tick(_trade(ts, "103"))
    assert len(out) == 3
    assert [b.close for b in out] == [Decimal("101"), Decimal("102"), Decimal("103")]


def test_reversal_requires_two_brick_widths() -> None:
    """After an up-brick, a small downtick should NOT emit a brick. A move of 2x
    brick_size emits the reversal."""
    agg = RenkoAggregator(brick_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    agg.on_tick(_trade(ts, "100"))
    agg.on_tick(_trade(ts, "101"))  # state: direction=1, last_close=101

    out = agg.on_tick(_trade(ts, "100.5"))  # only 0.5 down — no brick
    assert out == []

    out = agg.on_tick(_trade(ts, "99"))  # 2 down — reversal brick
    assert len(out) == 1
    b = out[0]
    # Reversal: open = last_close - brick = 100, close = 99
    assert b.open == Decimal("100")
    assert b.close == Decimal("99")


def test_volume_distributed_across_bricks() -> None:
    agg = RenkoAggregator(brick_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    agg.on_tick(_trade(ts, "100"))
    out = agg.on_tick(_trade(ts, "103", size="300"))
    assert len(out) == 3
    assert all(b.volume == Decimal("100") for b in out)


def test_invalid_brick_size_rejected() -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError):
        RenkoAggregator(brick_size=Decimal("0"))
    with _pytest.raises(ValueError):
        RenkoAggregator(brick_size=Decimal("-1"))
