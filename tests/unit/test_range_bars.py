"""Range-bar aggregator tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from core.schemas import AssetClass, Instrument, Tick
from data.aggregators import RangeBarAggregator

UTC = timezone.utc


def _spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")


def _trade(ts: datetime, price: str, size: str = "100") -> Tick:
    return Tick(instrument=_spy(), timestamp=ts, last_price=Decimal(price), last_size=Decimal(size))


def test_seed_no_emit() -> None:
    agg = RangeBarAggregator(range_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    out = agg.on_tick(_trade(ts, "100"))
    assert out == []


def test_within_range_no_emit() -> None:
    agg = RangeBarAggregator(range_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    agg.on_tick(_trade(ts, "100"))
    out = agg.on_tick(_trade(ts, "100.5"))
    assert out == []


def test_emits_when_range_exceeded() -> None:
    agg = RangeBarAggregator(range_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    agg.on_tick(_trade(ts, "100"))
    agg.on_tick(_trade(ts, "100.5"))
    out = agg.on_tick(_trade(ts, "101"))
    assert len(out) == 1
    bar = out[0]
    assert bar.open == Decimal("100")
    assert bar.high == Decimal("101")
    assert bar.low == Decimal("100")
    assert bar.close == Decimal("101")


def test_continues_after_emit() -> None:
    agg = RangeBarAggregator(range_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    agg.on_tick(_trade(ts, "100"))
    agg.on_tick(_trade(ts, "101"))  # bar 1 closes at 101
    out = agg.on_tick(_trade(ts, "99.99"))  # range = 101-99.99 = 1.01 ≥ 1
    assert len(out) == 1
    bar = out[0]
    assert bar.open == Decimal("101")
    assert bar.close == Decimal("99.99")


def test_flush_emits_in_progress_bar() -> None:
    agg = RangeBarAggregator(range_size=Decimal("1"))
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    agg.on_tick(_trade(ts, "100"))
    agg.on_tick(_trade(ts, "100.3"))
    flushed = agg.flush()
    assert len(flushed) == 1
    assert flushed[0].open == Decimal("100")
    assert flushed[0].close == Decimal("100.3")


def test_invalid_range_size_rejected() -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError):
        RangeBarAggregator(range_size=Decimal("0"))
