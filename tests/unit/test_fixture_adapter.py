"""Fixture adapter tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from core.schemas import AssetClass, BarTimeframe, Instrument
from data.adapters import FixtureAdapter
from data.base import HistoricalBarRequest

UTC = timezone.utc
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "spy_ticks_sample.jsonl"


def _spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")


@pytest.mark.asyncio
async def test_stream_ticks_yields_in_order_from_file() -> None:
    adapter = FixtureAdapter(FIXTURE_PATH, speed=0.0)
    await adapter.connect()
    ticks = []
    async for tick in adapter.stream_ticks([_spy()]):
        ticks.append(tick)
    await adapter.close()

    assert len(ticks) == 7
    # Chronological
    for prev, curr in zip(ticks, ticks[1:]):
        assert prev.timestamp <= curr.timestamp
    assert ticks[0].last_price == Decimal("555.00")
    assert ticks[-1].last_price == Decimal("555.50")


@pytest.mark.asyncio
async def test_stream_ticks_from_iterable_no_file() -> None:
    """In-memory iterable lets tests skip file I/O."""
    records = [
        {
            "type": "tick",
            "ts": "2026-05-25T13:30:00Z",
            "symbol": "SPY",
            "asset_class": "ETF",
            "last_price": "100.00",
            "last_size": "10",
        },
        {
            "type": "tick",
            "ts": "2026-05-25T13:30:01Z",
            "symbol": "SPY",
            "asset_class": "ETF",
            "last_price": "100.10",
            "last_size": "5",
        },
    ]
    adapter = FixtureAdapter(records, speed=0.0)
    await adapter.connect()
    ticks = [t async for t in adapter.stream_ticks([_spy()])]
    assert len(ticks) == 2
    assert ticks[0].last_price == Decimal("100.00")


@pytest.mark.asyncio
async def test_filter_by_instrument() -> None:
    records = [
        {"type": "tick", "ts": "2026-05-25T13:30:00Z", "symbol": "SPY",
         "asset_class": "ETF", "last_price": "100", "last_size": "1"},
        {"type": "tick", "ts": "2026-05-25T13:30:01Z", "symbol": "QQQ",
         "asset_class": "ETF", "last_price": "480", "last_size": "1"},
    ]
    adapter = FixtureAdapter(records, speed=0.0)
    await adapter.connect()
    ticks = [t async for t in adapter.stream_ticks([_spy()])]
    assert len(ticks) == 1
    assert ticks[0].instrument.symbol == "SPY"


@pytest.mark.asyncio
async def test_historical_bars_filter_by_range_and_tf() -> None:
    records = [
        {"type": "bar", "ts": "2026-05-25T13:30:00Z", "symbol": "SPY",
         "asset_class": "ETF", "timeframe": "1m",
         "open": "555.0", "high": "555.5", "low": "554.9", "close": "555.2", "volume": "1000"},
        {"type": "bar", "ts": "2026-05-25T13:31:00Z", "symbol": "SPY",
         "asset_class": "ETF", "timeframe": "1m",
         "open": "555.2", "high": "555.4", "low": "555.0", "close": "555.1", "volume": "800"},
        {"type": "bar", "ts": "2026-05-25T13:30:00Z", "symbol": "SPY",
         "asset_class": "ETF", "timeframe": "5m",
         "open": "555.0", "high": "555.5", "low": "554.9", "close": "555.2", "volume": "1000"},
    ]
    adapter = FixtureAdapter(records, speed=0.0)
    await adapter.connect()
    req = HistoricalBarRequest(
        instruments=[_spy()],
        timeframe=BarTimeframe.M1,
        start=datetime(2026, 5, 25, 13, 30, tzinfo=UTC),
        end=datetime(2026, 5, 25, 13, 31, tzinfo=UTC),
    )
    bars = [b async for b in adapter.historical_bars(req)]
    # End is exclusive so only the 13:30 1m bar matches.
    assert len(bars) == 1
    assert bars[0].timeframe is BarTimeframe.M1
    assert bars[0].timestamp == datetime(2026, 5, 25, 13, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_must_connect_before_use() -> None:
    adapter = FixtureAdapter([], speed=0.0)
    with pytest.raises(RuntimeError, match="not connected"):
        async for _ in adapter.stream_ticks([_spy()]):
            pass
