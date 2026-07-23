"""Ingestion daemon — DataAdapter → bus → aggregators → bus.

Wires an adapter's tick stream onto the bus and (optionally) runs in-process
aggregators that publish derived bars. Persistence is a separate consumer
(:mod:`data.writers`), so this module knows nothing about the DB.

Run via :func:`run_ingestion` for a one-shot async entrypoint, or use as a building
block from your own daemon process.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from decimal import Decimal

from core.bus import EventBus, Stream
from core.logging import get_logger
from core.schemas import Bar, BarTimeframe, Instrument, Tick
from data.aggregators import BarAggregator, TimeBarAggregator
from data.base import DataAdapter

log = get_logger(__name__)


# ---- serialization to bus payload ----


def _ser(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def tick_to_payload(t: Tick) -> dict[str, object]:
    return {
        "type": "tick",
        "instrument": _instrument_payload(t.instrument),
        "timestamp": t.timestamp.isoformat(),
        "bid": _ser(t.bid),
        "ask": _ser(t.ask),
        "bid_size": _ser(t.bid_size),
        "ask_size": _ser(t.ask_size),
        "last_price": _ser(t.last_price),
        "last_size": _ser(t.last_size),
        "exchange": t.exchange,
        "conditions": list(t.conditions),
    }


def bar_to_payload(b: Bar) -> dict[str, object]:
    return {
        "type": "bar",
        "instrument": _instrument_payload(b.instrument),
        "timeframe": b.timeframe.value,
        "timestamp": b.timestamp.isoformat(),
        "open": str(b.open),
        "high": str(b.high),
        "low": str(b.low),
        "close": str(b.close),
        "volume": str(b.volume),
        "vwap": _ser(b.vwap),
        "trade_count": b.trade_count,
    }


def _instrument_payload(i: Instrument) -> dict[str, object]:
    return {
        "symbol": i.symbol,
        "asset_class": i.asset_class.value,
        "venue": i.venue,
        "currency": i.currency,
    }


# ---- daemon ----


class IngestionPipeline:
    """Pumps an adapter's tick stream onto the bus and runs aggregators.

    Architecture:

      DataAdapter.stream_ticks ─► bus(Stream.TICKS) ─► aggregator(s) ─► bus(Stream.bars(tf))
                                                                       └─► more aggregators
    """

    def __init__(
        self,
        adapter: DataAdapter,
        bus: EventBus,
        instruments: Sequence[Instrument],
        *,
        time_bar_timeframes: Sequence[BarTimeframe] = (BarTimeframe.M1,),
        extra_aggregators: Sequence[tuple[str, BarAggregator]] = (),
    ) -> None:
        """
        Args:
            extra_aggregators: list of (output_stream_suffix, aggregator). Output
                stream becomes ``Stream.bars(suffix)``. Use for Renko, Range,
                Heikin-Ashi when configured.
        """
        self._adapter = adapter
        self._bus = bus
        self._instruments = list(instruments)
        self._time_bar_aggregators: dict[BarTimeframe, TimeBarAggregator] = {
            tf: TimeBarAggregator(tf) for tf in time_bar_timeframes
        }
        self._extra: list[tuple[str, BarAggregator]] = list(extra_aggregators)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        await self._adapter.connect()
        try:
            stream = self._adapter.stream_ticks(self._instruments)
            count = 0
            async for tick in stream:
                if self._stop.is_set():
                    break

                # 1) Publish raw tick.
                await self._bus.publish(Stream.TICKS, tick_to_payload(tick))
                count += 1

                # 2) Feed each time-bar aggregator; publish any bars that closed.
                for tf, agg in self._time_bar_aggregators.items():
                    for bar in agg.on_tick(tick):
                        await self._bus.publish(Stream.bars(tf.value), bar_to_payload(bar))

                # 3) Feed extra aggregators (Renko/Range)
                for suffix, agg in self._extra:
                    for bar in agg.on_tick(tick):
                        await self._bus.publish(Stream.bars(suffix), bar_to_payload(bar))

                if count % 1000 == 0:
                    log.info("ingestion_progress", ticks=count)

            # Final flush on shutdown
            await self._flush_all()
        finally:
            await self._adapter.close()

    async def _flush_all(self) -> None:
        for tf, agg in self._time_bar_aggregators.items():
            for bar in agg.flush():
                await self._bus.publish(Stream.bars(tf.value), bar_to_payload(bar))
        for suffix, agg in self._extra:
            for bar in agg.flush():
                await self._bus.publish(Stream.bars(suffix), bar_to_payload(bar))


async def run_ingestion(
    adapter: DataAdapter,
    bus: EventBus,
    instruments: Sequence[Instrument],
    *,
    timeframes: Sequence[BarTimeframe] = (BarTimeframe.M1,),
) -> None:
    """One-shot: connect bus + adapter, run until cancelled."""
    await bus.connect()
    pipeline = IngestionPipeline(adapter, bus, instruments, time_bar_timeframes=timeframes)
    task = asyncio.create_task(pipeline.run())

    try:
        await task
    except asyncio.CancelledError:
        pipeline.stop()
        with suppress(asyncio.CancelledError):
            await task
    finally:
        await bus.close()
