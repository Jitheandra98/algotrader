"""In-memory bus tests.

The in-memory bus is what the backtester and unit tests run against; it has to behave
like the production Redis bus enough that strategies tested here pass against Redis
too.
"""

from __future__ import annotations

import asyncio

import pytest

from core.bus import InMemoryBus, Stream


@pytest.mark.asyncio
async def test_publish_increments_stream() -> None:
    bus = InMemoryBus()
    await bus.connect()
    try:
        await bus.publish(Stream.SIGNALS, {"strategy_id": "s1", "direction": "LONG"})
        await bus.publish(Stream.SIGNALS, {"strategy_id": "s1", "direction": "EXIT"})
        assert bus.stream_length(Stream.SIGNALS) == 2
    finally:
        await bus.close()


@pytest.mark.asyncio
async def test_subscribe_delivers_messages() -> None:
    bus = InMemoryBus()
    await bus.connect()
    try:
        await bus.ensure_group(Stream.SIGNALS, "test-group")
        await bus.publish(Stream.SIGNALS, {"v": 1})
        await bus.publish(Stream.SIGNALS, {"v": 2})

        received: list[dict[str, object]] = []
        sub = bus.subscribe([Stream.SIGNALS], group="test-group", consumer="c1", block_ms=200)
        async for msg in sub:
            received.append(msg.payload)
            await bus.ack(msg.stream, "test-group", msg.id)
            if len(received) == 2:
                break

        assert [m["v"] for m in received] == [1, 2]
    finally:
        await bus.close()


@pytest.mark.asyncio
async def test_subscribe_starts_at_end_for_new_group() -> None:
    """New consumer groups should not receive messages published before they joined —
    matches Redis ``XGROUP CREATE ... $``."""
    bus = InMemoryBus()
    await bus.connect()
    try:
        await bus.publish(Stream.SIGNALS, {"v": 1})
        await bus.publish(Stream.SIGNALS, {"v": 2})

        # Group joins now, should only see message published *after* this.
        await bus.ensure_group(Stream.SIGNALS, "late-joiner")
        await bus.publish(Stream.SIGNALS, {"v": 3})

        sub = bus.subscribe([Stream.SIGNALS], group="late-joiner", consumer="c1", block_ms=100)
        seen = []
        async for m in sub:
            seen.append(m.payload["v"])
            await bus.ack(m.stream, "late-joiner", m.id)
            break

        assert seen == [3]
    finally:
        await bus.close()


@pytest.mark.asyncio
async def test_ack_clears_pending() -> None:
    bus = InMemoryBus()
    await bus.connect()
    try:
        await bus.ensure_group(Stream.SIGNALS, "g")
        await bus.publish(Stream.SIGNALS, {"v": 1})

        async for msg in bus.subscribe([Stream.SIGNALS], "g", "c1", block_ms=100):
            assert bus.pending_count(Stream.SIGNALS, "g") == 1
            await bus.ack(msg.stream, "g", msg.id)
            assert bus.pending_count(Stream.SIGNALS, "g") == 0
            break
    finally:
        await bus.close()


@pytest.mark.asyncio
async def test_block_timeout_loops() -> None:
    bus = InMemoryBus()
    await bus.connect()
    try:
        await bus.ensure_group(Stream.SIGNALS, "g")

        async def producer() -> None:
            await asyncio.sleep(0.05)
            await bus.publish(Stream.SIGNALS, {"v": 42})

        prod_task = asyncio.create_task(producer())
        async for msg in bus.subscribe([Stream.SIGNALS], "g", "c1", block_ms=200):
            assert msg.payload["v"] == 42
            await bus.ack(msg.stream, "g", msg.id)
            break
        await prod_task
    finally:
        await bus.close()
