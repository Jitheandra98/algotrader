"""Time-bar aggregator tests.

Lock the bucket-boundary semantics, OHLCV math, and VWAP accumulation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.schemas import AssetClass, BarTimeframe, Instrument, Tick
from data.aggregators import TimeBarAggregator

UTC = timezone.utc


def _spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")


def _trade(inst: Instrument, ts: datetime, price: str, size: str) -> Tick:
    return Tick(instrument=inst, timestamp=ts, last_price=Decimal(price), last_size=Decimal(size))


def test_single_bucket_no_emission_until_boundary_crossed() -> None:
    spy = _spy()
    agg = TimeBarAggregator(BarTimeframe.M1)
    base = datetime(2026, 5, 25, 13, 30, 0, tzinfo=UTC)

    closed = agg.on_tick(_trade(spy, base, "100", "10"))
    assert closed == []
    closed = agg.on_tick(_trade(spy, base + timedelta(seconds=30), "101", "20"))
    assert closed == []


def test_bucket_boundary_emits_previous_bar() -> None:
    spy = _spy()
    agg = TimeBarAggregator(BarTimeframe.M1)
    base = datetime(2026, 5, 25, 13, 30, 0, tzinfo=UTC)

    agg.on_tick(_trade(spy, base, "100", "10"))
    agg.on_tick(_trade(spy, base + timedelta(seconds=30), "101", "20"))
    closed = agg.on_tick(_trade(spy, base + timedelta(minutes=1, seconds=1), "102", "5"))

    assert len(closed) == 1
    bar = closed[0]
    assert bar.timeframe is BarTimeframe.M1
    assert bar.timestamp == base
    assert bar.open == Decimal("100")
    assert bar.high == Decimal("101")
    assert bar.low == Decimal("100")
    assert bar.close == Decimal("101")
    assert bar.volume == Decimal("30")
    assert bar.trade_count == 2
    # VWAP = (100*10 + 101*20) / (10+20) = 3020 / 30
    assert bar.vwap == Decimal("3020") / Decimal("30")


def test_flush_emits_in_progress_bar() -> None:
    spy = _spy()
    agg = TimeBarAggregator(BarTimeframe.M1)
    base = datetime(2026, 5, 25, 13, 30, 0, tzinfo=UTC)
    agg.on_tick(_trade(spy, base, "100", "10"))
    flushed = agg.flush()
    assert len(flushed) == 1
    assert flushed[0].timestamp == base
    # And state is cleared
    assert agg.flush() == []


def test_multiple_instruments_isolated() -> None:
    spy = _spy()
    qqq = Instrument(symbol="QQQ", asset_class=AssetClass.ETF, venue="NASDAQ")
    agg = TimeBarAggregator(BarTimeframe.M1)
    base = datetime(2026, 5, 25, 13, 30, 0, tzinfo=UTC)

    agg.on_tick(_trade(spy, base, "555", "100"))
    agg.on_tick(_trade(qqq, base, "480", "50"))
    closed = agg.on_tick(_trade(spy, base + timedelta(minutes=1, seconds=1), "556", "10"))

    assert len(closed) == 1
    assert closed[0].instrument.symbol == "SPY"


def test_quote_only_tick_uses_mid_price() -> None:
    spy = _spy()
    agg = TimeBarAggregator(BarTimeframe.M1)
    base = datetime(2026, 5, 25, 13, 30, 0, tzinfo=UTC)
    quote = Tick(
        instrument=spy,
        timestamp=base,
        bid=Decimal("99.90"),
        ask=Decimal("100.10"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
    )
    agg.on_tick(quote)
    flushed = agg.flush()
    assert len(flushed) == 1
    assert flushed[0].open == Decimal("100.00")
    # No trades happened — VWAP must be None and volume zero.
    assert flushed[0].vwap is None
    assert flushed[0].volume == 0


def test_skip_intermediate_empty_buckets() -> None:
    """If no ticks land in a bucket, we emit NO bar for it — gaps are detectable
    from timestamps downstream."""
    spy = _spy()
    agg = TimeBarAggregator(BarTimeframe.M1)
    base = datetime(2026, 5, 25, 13, 30, 0, tzinfo=UTC)
    agg.on_tick(_trade(spy, base, "100", "10"))
    closed = agg.on_tick(_trade(spy, base + timedelta(minutes=5, seconds=1), "105", "10"))
    assert len(closed) == 1  # only the 13:30 bar
    assert closed[0].timestamp == base
