"""Event bus abstractions and implementations.

Higher layers depend on the ``EventBus`` Protocol only. Concrete implementations
(``RedisStreamBus``, ``InMemoryBus``) are wired up at startup.
"""

from core.bus.base import (
    BusMessage,
    EventBus,
    Stream,
)
from core.bus.memory import InMemoryBus
from core.bus.redis_bus import RedisStreamBus

__all__ = [
    "BusMessage",
    "EventBus",
    "InMemoryBus",
    "RedisStreamBus",
    "Stream",
]
