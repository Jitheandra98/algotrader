"""In-memory event bus.

Used by tests and the single-process backtester so we don't need Redis to run a
backtest. Behavior approximates a Redis stream consumer group:
  - per-stream ordered log
  - per-(stream, group) read cursor
  - unacked messages are re-delivered on reconnect via a pending set (kept simple here)

NOT production-safe — no persistence, no cross-process delivery.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from core.bus.base import BusMessage, EventBus


class InMemoryBus(EventBus):
    def __init__(self) -> None:
        self._streams: dict[str, list[BusMessage]] = defaultdict(list)
        self._cursors: dict[tuple[str, str], int] = {}  # (stream, group) -> next index
        self._pending: dict[tuple[str, str], set[str]] = defaultdict(set)
        self._groups: set[tuple[str, str]] = set()
        self._events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self._counter = itertools.count()
        self._connected = False
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False
        # Wake any waiters so they can exit cleanly.
        for ev in self._events.values():
            ev.set()

    async def ensure_group(self, stream: str, group: str) -> None:
        self._groups.add((stream, group))
        # Start groups at the end of the stream by default (Redis '$').
        self._cursors.setdefault((stream, group), len(self._streams[stream]))

    async def publish(
        self,
        stream: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> str:
        if not self._connected:
            raise RuntimeError("InMemoryBus not connected")
        mid = f"{next(self._counter)}-{uuid.uuid4().hex[:8]}"
        msg = BusMessage(id=mid, stream=stream, payload=dict(payload), headers=dict(headers or {}))
        async with self._lock:
            self._streams[stream].append(msg)
        self._events[stream].set()
        return mid

    async def subscribe(
        self,
        streams: list[str],
        group: str,
        consumer: str,
        block_ms: int = 5_000,
        count: int = 100,
    ) -> AsyncIterator[BusMessage]:
        for s in streams:
            await self.ensure_group(s, group)

        while self._connected:
            delivered = 0
            for s in streams:
                cursor = self._cursors[(s, group)]
                buf = self._streams[s]
                if cursor < len(buf):
                    batch = buf[cursor : cursor + count]
                    for m in batch:
                        self._pending[(s, group)].add(m.id)
                        yield m
                        delivered += 1
                    self._cursors[(s, group)] = cursor + len(batch)

            if delivered == 0:
                # Wait for any of the subscribed streams to receive new data.
                # We rely on a single event per stream and clear on consume.
                waiters = [self._events[s] for s in streams]
                for ev in waiters:
                    ev.clear()
                try:
                    await asyncio.wait_for(
                        asyncio.wait(
                            [asyncio.create_task(ev.wait()) for ev in waiters],
                            return_when=asyncio.FIRST_COMPLETED,
                        ),
                        timeout=block_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    # Just loop — caller's async for will iterate again.
                    continue

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        self._pending[(stream, group)].discard(message_id)

    # ---- Test helpers ----

    def stream_length(self, stream: str) -> int:
        return len(self._streams[stream])

    def pending_count(self, stream: str, group: str) -> int:
        return len(self._pending[(stream, group)])
