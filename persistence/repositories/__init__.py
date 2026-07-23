"""Per-aggregate repositories.

  - :class:`TickRepository` — bulk-insert ticks (M2)
  - :class:`BarRepository`  — upsert bars idempotently (M2)
  - Signals, orders, fills, positions: M5/M7.
"""

from persistence.repositories.bars import BarRepository
from persistence.repositories.base import Repository
from persistence.repositories.ticks import TickRepository

__all__ = ["BarRepository", "Repository", "TickRepository"]
