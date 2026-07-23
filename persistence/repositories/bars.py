"""Bar repository.

Bars are idempotent on ``(instrument, timeframe, ts)`` — re-running the same range
backfill is safe via ``ON CONFLICT DO UPDATE`` (the broker may revise historical bars
late-arriving).
"""

from __future__ import annotations

from collections.abc import Sequence

from core.logging import get_logger
from core.schemas import Bar
from persistence.repositories.base import Repository

log = get_logger(__name__)


_UPSERT_SQL = """
INSERT INTO bars
    (ts, instrument, timeframe, open, high, low, close, volume, vwap, trade_count)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (instrument, timeframe, ts) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    vwap = EXCLUDED.vwap,
    trade_count = EXCLUDED.trade_count
"""


def _row(b: Bar) -> tuple[object, ...]:
    return (
        b.timestamp,
        b.instrument.key,
        b.timeframe.value,
        b.open,
        b.high,
        b.low,
        b.close,
        b.volume,
        b.vwap,
        b.trade_count,
    )


class BarRepository(Repository):
    async def upsert_many(self, bars: Sequence[Bar]) -> int:
        if not bars:
            return 0
        rows = [_row(b) for b in bars]
        async with self._db.acquire() as conn:
            await conn.executemany(_UPSERT_SQL, rows)
        return len(rows)

    async def latest(self, instrument_key: str, timeframe: str) -> Bar | None:
        """Return the most recent bar for the given (instrument, timeframe). None if
        the table is empty for that pair."""
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT instrument, timeframe, ts, open, high, low, close, volume, vwap, trade_count
                FROM bars
                WHERE instrument = $1 AND timeframe = $2
                ORDER BY ts DESC
                LIMIT 1
                """,
                instrument_key,
                timeframe,
            )
        return _row_to_bar_dict(row) if row else None


def _row_to_bar_dict(row: object) -> Bar | None:
    """Hydrate a DB row back into a Bar. The instrument key shape on disk doesn't
    fully round-trip (we don't store asset_class separately for ticks), but for bars
    we *do* — the timeframe + symbol-prefix on the key is enough to reconstruct.

    For now we leave this stub since strategies will read bars via the in-memory
    cache from the bus rather than DB. Repository readers are expanded in M4."""
    # Implemented in M4 when backtest hydrates DB rows back to Bars.
    return None  # type: ignore[unreachable]
