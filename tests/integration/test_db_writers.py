"""Integration tests for tick/bar writers against a live TimescaleDB.

Marked ``integration`` — run with ``make test-integration`` after ``make up``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.schemas import AssetClass, Bar, BarTimeframe, Instrument, Tick
from persistence.db import Database
from persistence.repositories import BarRepository, TickRepository

UTC = timezone.utc

pytestmark = pytest.mark.integration


def _spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")


@pytest.mark.asyncio
async def test_tick_repo_inserts(db: Database) -> None:
    repo = TickRepository(db)
    spy = _spy()
    base = datetime(2026, 5, 25, 13, 30, 0, tzinfo=UTC)
    ticks = [
        Tick(
            instrument=spy,
            timestamp=base.replace(second=i),
            last_price=Decimal(f"{555 + i * 0.01}"),
            last_size=Decimal("100"),
        )
        for i in range(5)
    ]
    n = await repo.insert_many(ticks)
    assert n == 5

    count = await repo.count_for(spy.key)
    assert count >= 5


@pytest.mark.asyncio
async def test_bar_repo_upserts_idempotently(db: Database) -> None:
    """Upserting the same (instrument, timeframe, ts) twice must not produce two rows."""
    repo = BarRepository(db)
    spy = _spy()
    ts = datetime(2026, 5, 25, 14, 0, 0, tzinfo=UTC)
    bar = Bar(
        instrument=spy,
        timeframe=BarTimeframe.M1,
        timestamp=ts,
        open=Decimal("555.00"),
        high=Decimal("555.20"),
        low=Decimal("554.95"),
        close=Decimal("555.10"),
        volume=Decimal("12345"),
    )
    await repo.upsert_many([bar])
    await repo.upsert_many([bar])  # second insert — should upsert, not duplicate

    async with db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM bars WHERE instrument = $1 AND timeframe = $2 AND ts = $3",
            spy.key,
            BarTimeframe.M1.value,
            ts,
        )
    assert count == 1
