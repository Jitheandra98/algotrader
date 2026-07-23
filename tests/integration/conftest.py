"""Integration test fixtures.

Skipped by default. To run: ``make up`` first, then ``make test-integration``.

Env vars (with sensible local defaults):
  REDIS_URL=redis://localhost:6379/0
  DATABASE_URL=postgresql://algotrader:algotrader@localhost:5432/algotrader
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from core.bus import RedisStreamBus
from persistence.db import Database

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://algotrader:algotrader@localhost:5432/algotrader"
)


@pytest_asyncio.fixture
async def redis_bus() -> AsyncIterator[RedisStreamBus]:
    bus = RedisStreamBus(REDIS_URL)
    try:
        await bus.connect()
    except Exception as exc:
        pytest.skip(f"Redis not available at {REDIS_URL}: {exc}")
    try:
        yield bus
    finally:
        await bus.close()


@pytest_asyncio.fixture
async def db() -> AsyncIterator[Database]:
    database = Database(DATABASE_URL, min_size=1, max_size=4)
    try:
        await database.connect()
    except Exception as exc:
        pytest.skip(f"Postgres not available at {DATABASE_URL}: {exc}")
    try:
        yield database
    finally:
        await database.close()
