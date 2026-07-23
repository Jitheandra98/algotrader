"""Event bus interface.

A bus is the *only* way components communicate. Each component publishes typed events
to a named stream and subscribes to streams it cares about. Consumer groups give us
at-least-once delivery; consumers must be idempotent (use ``BusMessage.id`` or
domain ids like ``client_order_id`` for dedup).

Streams (see ``Stream``) are namespaced by layer:

  data.ticks         — raw normalized ticks from data adapters
  data.bars.{tf}     — aggregated bars at timeframe ``tf``
  signals            — strategy signals
  orders.intent      — order intents from risk (post-sized signal)
  orders.events      — order status updates from execution
  fills              — fills from execution
  alerts             — monitoring alerts

The Protocol is intentionally minimal — concrete implementations layer on top of
``publish``/``subscribe``/``ack``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Stream:
    """Stream name constants. Use these instead of bare strings to avoid typos."""

    TICKS: str = "data.ticks"
    BARS: str = "data.bars"  # callers append `.1m`, `.5m`, etc.
    SIGNALS: str = "signals"
    ORDER_INTENTS: str = "orders.intent"
    ORDER_EVENTS: str = "orders.events"
    FILLS: str = "fills"
    ALERTS: str = "alerts"
    HEARTBEATS: str = "system.heartbeats"

    @staticmethod
    def bars(timeframe: str) -> str:
        return f"data.bars.{timeframe}"


@dataclass(frozen=True, slots=True)
class BusMessage:
    """A message as received from the bus. ``id`` is the bus-assigned id (Redis stream
    id, Kafka offset, etc.) used for ack. ``payload`` is the JSON-decoded body."""

    id: str
    stream: str
    payload: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class EventBus(ABC):
    """Abstract bus interface.

    Implementations:
      - ``RedisStreamBus``  — production, persistent, at-least-once via consumer groups
      - ``InMemoryBus``     — tests / single-process embedded mode
    """

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def publish(
        self,
        stream: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> str:
        """Append a message to ``stream``. Returns the bus-assigned message id."""

    @abstractmethod
    async def subscribe(
        self,
        streams: list[str],
        group: str,
        consumer: str,
        block_ms: int = 5_000,
        count: int = 100,
    ) -> AsyncIterator[BusMessage]:
        """Iterate messages from ``streams`` as part of consumer ``group``.

        ``block_ms`` is the read timeout per poll; on timeout the iterator yields
        nothing and loops again, so callers can use ``async for`` with no special
        timeout handling.
        """
        # NB: an abstract async generator's body must contain `yield` so Python
        # recognises it. The actual generator behavior is implemented in subclasses.
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]

    @abstractmethod
    async def ack(self, stream: str, group: str, message_id: str) -> None: ...

    @abstractmethod
    async def ensure_group(self, stream: str, group: str) -> None:
        """Create the consumer group if it doesn't exist. Idempotent."""
