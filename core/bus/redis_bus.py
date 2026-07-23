"""Redis Streams implementation of ``EventBus``.

Design notes:
  - Payloads are serialized as a single JSON blob in a Redis field named ``data``.
    Headers go in field ``headers``. Keeping it one-field-per-concept makes Redis CLI
    debugging readable while still allowing arbitrary structure.
  - ``stream_max_len`` from config trims old entries on each publish (approximate
    trimming with ``~`` for performance).
  - ``XREADGROUP`` with ``BLOCK`` gives us efficient long-polling. The block timeout
    is taken from config; on timeout we loop so subscribers can shut down gracefully.
  - Consumer groups are created lazily via ``ensure_group``. Idempotent.

This implementation is async-only and uses ``redis.asyncio``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import orjson
import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from core.bus.base import BusMessage, EventBus
from core.logging import get_logger

log = get_logger(__name__)


class RedisStreamBus(EventBus):
    def __init__(
        self,
        url: str,
        *,
        stream_max_len: int = 1_000_000,
        block_ms_default: int = 5_000,
    ) -> None:
        self._url = url
        self._stream_max_len = stream_max_len
        self._block_ms_default = block_ms_default
        self._client: aioredis.Redis | None = None
        self._closed = False

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = aioredis.from_url(self._url, encoding=None, decode_responses=False)
        await self._client.ping()
        log.info("bus_connected", url=self._url)

    async def close(self) -> None:
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            log.info("bus_closed")

    def _require_client(self) -> aioredis.Redis:
        if self._client is None:
            raise RuntimeError("RedisStreamBus not connected — call connect() first")
        return self._client

    async def ensure_group(self, stream: str, group: str) -> None:
        client = self._require_client()
        try:
            # ``$`` = start from new messages; ``mkstream=True`` creates the stream if needed.
            await client.xgroup_create(name=stream, groupname=group, id="$", mkstream=True)
            log.info("bus_group_created", stream=stream, group=group)
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                return  # already exists; fine
            raise

    async def publish(
        self,
        stream: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> str:
        client = self._require_client()
        fields: dict[bytes, bytes] = {
            b"data": orjson.dumps(payload),
        }
        if headers:
            fields[b"headers"] = orjson.dumps(headers)
        msg_id_bytes = await client.xadd(
            name=stream,
            fields=fields,
            maxlen=self._stream_max_len,
            approximate=True,
        )
        return msg_id_bytes.decode() if isinstance(msg_id_bytes, bytes) else str(msg_id_bytes)

    async def subscribe(
        self,
        streams: list[str],
        group: str,
        consumer: str,
        block_ms: int = 5_000,
        count: int = 100,
    ) -> AsyncIterator[BusMessage]:
        client = self._require_client()
        for s in streams:
            await self.ensure_group(s, group)

        # ``>`` means: deliver only new messages not yet delivered to any consumer in this group.
        streams_map = {s.encode(): b">" for s in streams}

        while not self._closed:
            try:
                resp = await client.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams=streams_map,
                    count=count,
                    block=block_ms,
                )
            except aioredis.ConnectionError as exc:
                log.error("bus_read_connection_error", error=str(exc))
                await asyncio.sleep(1.0)
                continue

            if not resp:
                # Block timeout — let caller's loop re-enter.
                continue

            for stream_name_b, entries in resp:
                stream_name = stream_name_b.decode()
                for msg_id_b, fields in entries:
                    msg_id = msg_id_b.decode()
                    data_raw = fields.get(b"data")
                    headers_raw = fields.get(b"headers")
                    payload = orjson.loads(data_raw) if data_raw else {}
                    headers = orjson.loads(headers_raw) if headers_raw else {}
                    yield BusMessage(
                        id=msg_id, stream=stream_name, payload=payload, headers=headers
                    )

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        client = self._require_client()
        await client.xack(stream, group, message_id)
