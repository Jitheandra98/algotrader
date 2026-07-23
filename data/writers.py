"""Bus-to-DB writer service.

Subscribes to ``data.ticks`` and ``data.bars.*`` streams and bulk-persists batches to
TimescaleDB. Batches are flushed either when ``max_batch`` is reached or after
``flush_interval_ms``, whichever comes first — gives us throughput on busy markets
without unbounded latency on quiet ones.

Designed to run as its own asyncio task so it doesn't share a queue with the
ingestion adapter. The bus provides the back-pressure: if the writer falls behind,
the Redis stream grows (up to ``stream_max_len``) and old entries are trimmed.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal

from core.bus import EventBus, Stream
from core.logging import get_logger
from core.schemas import AssetClass, Bar, BarTimeframe, Instrument, Tick
from persistence.repositories import BarRepository, TickRepository

log = get_logger(__name__)


class TickWriter:
    def __init__(
        self,
        bus: EventBus,
        repo: TickRepository,
        *,
        group: str = "writer-ticks",
        consumer: str = "w1",
        max_batch: int = 1000,
        flush_interval_ms: int = 500,
    ) -> None:
        self._bus = bus
        self._repo = repo
        self._group = group
        self._consumer = consumer
        self._max_batch = max_batch
        self._flush_interval_ms = flush_interval_ms

    async def run(self) -> None:
        await self._bus.ensure_group(Stream.TICKS, self._group)
        batch: list[Tick] = []
        ack_ids: list[str] = []
        last_flush = time.monotonic()

        async for msg in self._bus.subscribe(
            [Stream.TICKS],
            group=self._group,
            consumer=self._consumer,
            block_ms=200,
            count=self._max_batch,
        ):
            try:
                batch.append(_tick_from_payload(msg.payload))
                ack_ids.append(msg.id)
            except Exception:
                log.exception("tick_decode_failed", msg_id=msg.id)
                # Ack anyway so we don't loop on a poison-pill message.
                await self._bus.ack(msg.stream, self._group, msg.id)
                continue

            now = time.monotonic()
            if len(batch) >= self._max_batch or (now - last_flush) * 1000 >= self._flush_interval_ms:
                await self._flush(batch, ack_ids)
                batch.clear()
                ack_ids.clear()
                last_flush = now

    async def _flush(self, batch: list[Tick], ack_ids: list[str]) -> None:
        if not batch:
            return
        try:
            await self._repo.copy_many(batch)
        except Exception:
            log.exception("tick_flush_failed", n=len(batch))
            return  # leave unacked; will redeliver
        for mid in ack_ids:
            await self._bus.ack(Stream.TICKS, self._group, mid)
        log.debug("ticks_persisted", n=len(batch))


class BarWriter:
    def __init__(
        self,
        bus: EventBus,
        repo: BarRepository,
        *,
        timeframes: list[BarTimeframe],
        group: str = "writer-bars",
        consumer: str = "w1",
        max_batch: int = 500,
        flush_interval_ms: int = 1000,
    ) -> None:
        self._bus = bus
        self._repo = repo
        self._streams = [Stream.bars(tf.value) for tf in timeframes]
        self._group = group
        self._consumer = consumer
        self._max_batch = max_batch
        self._flush_interval_ms = flush_interval_ms

    async def run(self) -> None:
        for s in self._streams:
            await self._bus.ensure_group(s, self._group)
        batch: list[Bar] = []
        ack: list[tuple[str, str]] = []
        last_flush = time.monotonic()

        async for msg in self._bus.subscribe(
            self._streams,
            group=self._group,
            consumer=self._consumer,
            block_ms=200,
            count=self._max_batch,
        ):
            try:
                batch.append(_bar_from_payload(msg.payload))
                ack.append((msg.stream, msg.id))
            except Exception:
                log.exception("bar_decode_failed", msg_id=msg.id)
                await self._bus.ack(msg.stream, self._group, msg.id)
                continue

            now = time.monotonic()
            if len(batch) >= self._max_batch or (now - last_flush) * 1000 >= self._flush_interval_ms:
                await self._flush(batch, ack)
                batch.clear()
                ack.clear()
                last_flush = now

    async def _flush(self, batch: list[Bar], ack: list[tuple[str, str]]) -> None:
        if not batch:
            return
        try:
            await self._repo.upsert_many(batch)
        except Exception:
            log.exception("bar_flush_failed", n=len(batch))
            return
        for stream, mid in ack:
            await self._bus.ack(stream, self._group, mid)
        log.debug("bars_persisted", n=len(batch))


# ---- payload codecs ----


def _tick_from_payload(p: dict[str, object]) -> Tick:
    inst = _instrument_from_payload(p["instrument"])  # type: ignore[arg-type]
    return Tick(
        instrument=inst,
        timestamp=p["timestamp"],  # type: ignore[arg-type]
        bid=Decimal(p["bid"]) if p.get("bid") is not None else None,
        ask=Decimal(p["ask"]) if p.get("ask") is not None else None,
        bid_size=Decimal(p["bid_size"]) if p.get("bid_size") is not None else None,
        ask_size=Decimal(p["ask_size"]) if p.get("ask_size") is not None else None,
        last_price=Decimal(p["last_price"]) if p.get("last_price") is not None else None,
        last_size=Decimal(p["last_size"]) if p.get("last_size") is not None else None,
        exchange=p.get("exchange"),  # type: ignore[arg-type]
        conditions=tuple(p.get("conditions") or ()),  # type: ignore[arg-type]
    )


def _bar_from_payload(p: dict[str, object]) -> Bar:
    inst = _instrument_from_payload(p["instrument"])  # type: ignore[arg-type]
    return Bar(
        instrument=inst,
        timeframe=BarTimeframe(p["timeframe"]),  # type: ignore[arg-type]
        timestamp=p["timestamp"],  # type: ignore[arg-type]
        open=Decimal(p["open"]),  # type: ignore[arg-type]
        high=Decimal(p["high"]),  # type: ignore[arg-type]
        low=Decimal(p["low"]),  # type: ignore[arg-type]
        close=Decimal(p["close"]),  # type: ignore[arg-type]
        volume=Decimal(p["volume"]),  # type: ignore[arg-type]
        vwap=Decimal(p["vwap"]) if p.get("vwap") is not None else None,
        trade_count=int(p.get("trade_count", 0)),  # type: ignore[arg-type]
    )


def _instrument_from_payload(p: dict[str, object]) -> Instrument:
    return Instrument(
        symbol=p["symbol"],  # type: ignore[arg-type]
        asset_class=AssetClass(p["asset_class"]),  # type: ignore[arg-type]
        venue=p.get("venue"),  # type: ignore[arg-type]
        currency=p.get("currency", "USD"),  # type: ignore[arg-type]
    )
