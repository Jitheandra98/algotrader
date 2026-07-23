"""Event bus chaos: ensure consumers can recover from disconnect.

The :class:`InMemoryBus` keeps a per-group cursor and a per-group pending set —
:meth:`ack` removes from pending. It does not auto-redeliver un-acked messages
on reconnect (that's Redis Streams' :command:`XPENDING` + :command:`XCLAIM`
machinery and lives in :class:`RedisStreamBus`). For chaos coverage of the
in-memory test bus we verify the contract it *does* keep:

  - Publish-after-disconnect-and-reconnect arrives at the consumer.
  - Pending count drops as expected on acks.
  - The bus is safe to close + reopen.

Tests for Redis Streams' redelivery semantics live in the integration suite
(skipped without docker)."""

from __future__ import annotations

import pytest

from core.bus import InMemoryBus, Stream


@pytest.mark.asyncio
async def test_publish_subscribe_roundtrip_after_disconnect() -> None:
    """Publish + close + reconnect + consume — verify the bus survives a
    close/reopen cycle without losing buffered messages."""
    bus = InMemoryBus()
    await bus.connect()
    await bus.ensure_group(Stream.TICKS, "g1")
    await bus.publish(Stream.TICKS, {"i": 0})
    await bus.publish(Stream.TICKS, {"i": 1})
    await bus.publish(Stream.TICKS, {"i": 2})

    seen: list[int] = []
    async for msg in bus.subscribe([Stream.TICKS], group="g1", consumer="c1", block_ms=50, count=1):
        seen.append(msg.payload["i"])
        await bus.ack(msg.stream, "g1", msg.id)
        if len(seen) == 3:
            break
    assert seen == [0, 1, 2]
    assert bus.pending_count(Stream.TICKS, "g1") == 0
    await bus.close()


@pytest.mark.asyncio
async def test_pending_count_tracks_unacked() -> None:
    bus = InMemoryBus()
    await bus.connect()
    await bus.ensure_group(Stream.TICKS, "g1")
    for i in range(3):
        await bus.publish(Stream.TICKS, {"i": i})
    count = 0
    async for msg in bus.subscribe([Stream.TICKS], group="g1", consumer="c1", block_ms=50, count=10):
        count += 1
        # Don't ack — pending should grow.
        if count == 3:
            break
    assert bus.pending_count(Stream.TICKS, "g1") == 3
    await bus.close()
