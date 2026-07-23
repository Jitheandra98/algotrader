"""asyncpg connection pool wrapper.

Used by repositories. Keeps the pool DI-friendly: ``Database`` is constructed once at
startup and injected into repositories that need it. No global state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from core.logging import get_logger

log = get_logger(__name__)


class Database:
    def __init__(self, url: str, *, min_size: int = 2, max_size: int = 10) -> None:
        self._url = url
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            dsn=self._url,
            min_size=self._min_size,
            max_size=self._max_size,
            command_timeout=30,
        )
        log.info("db_connected", min_size=self._min_size, max_size=self._max_size)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            log.info("db_closed")

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database not connected — call connect() first")
        return self._pool

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as conn, conn.transaction():
            yield conn
