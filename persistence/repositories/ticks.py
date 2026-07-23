"""Tick repository — bulk inserts to the TimescaleDB ``ticks`` hypertable.

Throughput is the primary concern. We accept ticks in batches and use asyncpg's
``executemany`` (or ``copy_records_to_table`` for the largest batches). Schema is
append-only — ticks are not updated after insertion.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from core.logging import get_logger
from core.schemas import Tick
from persistence.repositories.base import Repository

log = get_logger(__name__)


_INSERT_SQL = """
INSERT INTO ticks
    (ts, instrument, bid, ask, bid_size, ask_size, last_price, last_size, exchange, conditions)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
"""


def _row(t: Tick) -> tuple[object, ...]:
    return (
        t.timestamp,
        t.instrument.key,
        t.bid,
        t.ask,
        t.bid_size,
        t.ask_size,
        t.last_price,
        t.last_size,
        t.exchange,
        list(t.conditions) if t.conditions else None,
    )


class TickRepository(Repository):
    async def insert_many(self, ticks: Sequence[Tick]) -> int:
        if not ticks:
            return 0
        rows = [_row(t) for t in ticks]
        async with self._db.acquire() as conn:
            await conn.executemany(_INSERT_SQL, rows)
        return len(rows)

    async def copy_many(self, ticks: Iterable[Tick]) -> int:
        """High-throughput COPY path for very large batches (10k+ rows)."""
        rows = [_row(t) for t in ticks]
        if not rows:
            return 0
        async with self._db.acquire() as conn:
            await conn.copy_records_to_table(
                "ticks",
                records=rows,
                columns=[
                    "ts", "instrument", "bid", "ask", "bid_size", "ask_size",
                    "last_price", "last_size", "exchange", "conditions",
                ],
            )
        return len(rows)

    async def count_for(self, instrument_key: str) -> int:
        async with self._db.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COUNT(*) FROM ticks WHERE instrument = $1",
                instrument_key,
            )
        return int(val or 0)
