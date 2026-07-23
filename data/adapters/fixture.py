"""Fixture replay adapter.

Reads JSONL recordings of ticks and bars and emits them as if from a live feed.
Used by:

- the backtester (M4) for historical replay,
- unit tests that need deterministic input,
- integration tests when an exchange is offline.

JSONL schema (one record per line):

  {"type": "tick", "ts": "2026-05-25T13:30:00.123Z", "symbol": "SPY",
   "asset_class": "ETF", "bid": "555.10", "ask": "555.11",
   "bid_size": "100", "ask_size": "200",
   "last_price": "555.10", "last_size": "100"}

  {"type": "bar", "ts": "2026-05-25T13:30:00Z", "symbol": "SPY",
   "asset_class": "ETF", "timeframe": "1m",
   "open": "555.00", "high": "555.20", "low": "554.95",
   "close": "555.10", "volume": "12345", "vwap": "555.07"}

Records must be in chronological order; the adapter trusts the file.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import orjson

from core.logging import get_logger
from core.schemas import AssetClass, Bar, BarTimeframe, Instrument, Tick
from data.base import DataAdapter, HistoricalBarRequest

log = get_logger(__name__)


class FixtureAdapter(DataAdapter):
    """Replays recorded ticks/bars at configurable speed."""

    name = "fixture"

    def __init__(
        self,
        path: Path | str | Iterable[dict[str, Any]],
        *,
        speed: float = 0.0,
    ) -> None:
        """
        Args:
            path: JSONL file path OR an iterable of pre-parsed dict records (for tests).
            speed: 0 = as-fast-as-possible (no sleeps). Otherwise, the multiplier
                applied to the inter-record wall-clock gap. 1.0 = real-time.
        """
        self._source = path
        self._speed = speed
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def close(self) -> None:
        self._connected = False

    # ---- core iterator (private) ----

    def _records(self) -> Iterable[dict[str, Any]]:
        src = self._source
        if isinstance(src, (str, Path)):
            with Path(src).open("rb") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith(b"#"):
                        continue
                    yield orjson.loads(line)
        else:
            yield from src

    @staticmethod
    def _parse_instrument(rec: dict[str, Any]) -> Instrument:
        return Instrument(
            symbol=rec["symbol"],
            asset_class=AssetClass(rec.get("asset_class", "EQUITY")),
            venue=rec.get("venue"),
            currency=rec.get("currency", "USD"),
        )

    @staticmethod
    def _parse_ts(ts: str) -> datetime:
        # orjson preserves microseconds; standard isoformat with Z works.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)

    def _parse_tick(self, rec: dict[str, Any]) -> Tick:
        return Tick(
            instrument=self._parse_instrument(rec),
            timestamp=self._parse_ts(rec["ts"]),
            bid=Decimal(rec["bid"]) if rec.get("bid") is not None else None,
            ask=Decimal(rec["ask"]) if rec.get("ask") is not None else None,
            bid_size=Decimal(rec["bid_size"]) if rec.get("bid_size") is not None else None,
            ask_size=Decimal(rec["ask_size"]) if rec.get("ask_size") is not None else None,
            last_price=Decimal(rec["last_price"]) if rec.get("last_price") is not None else None,
            last_size=Decimal(rec["last_size"]) if rec.get("last_size") is not None else None,
            exchange=rec.get("exchange"),
            conditions=tuple(rec.get("conditions") or ()),
        )

    def _parse_bar(self, rec: dict[str, Any]) -> Bar:
        return Bar(
            instrument=self._parse_instrument(rec),
            timeframe=BarTimeframe(rec["timeframe"]),
            timestamp=self._parse_ts(rec["ts"]),
            open=Decimal(rec["open"]),
            high=Decimal(rec["high"]),
            low=Decimal(rec["low"]),
            close=Decimal(rec["close"]),
            volume=Decimal(rec["volume"]),
            vwap=Decimal(rec["vwap"]) if rec.get("vwap") is not None else None,
            trade_count=int(rec.get("trade_count", 0)),
        )

    async def _maybe_sleep(self, prev_ts: datetime | None, curr_ts: datetime) -> None:
        if self._speed <= 0 or prev_ts is None:
            return
        gap = (curr_ts - prev_ts).total_seconds() / self._speed
        if gap > 0:
            await asyncio.sleep(gap)

    # ---- public ----

    async def historical_bars(self, request: HistoricalBarRequest) -> AsyncIterator[Bar]:
        if not self._connected:
            raise RuntimeError("FixtureAdapter not connected — call connect() first")
        sym_set = {i.key for i in request.instruments}
        count = 0
        for rec in self._records():
            if rec.get("type") != "bar":
                continue
            if rec.get("timeframe") != request.timeframe.value:
                continue
            ts = self._parse_ts(rec["ts"])
            if ts < request.start or ts >= request.end:
                continue
            bar = self._parse_bar(rec)
            if bar.instrument.key not in sym_set:
                continue
            yield bar
            count += 1
            if request.limit is not None and count >= request.limit:
                return

    async def stream_ticks(self, instruments: Sequence[Instrument]) -> AsyncIterator[Tick]:
        if not self._connected:
            raise RuntimeError("FixtureAdapter not connected — call connect() first")
        sym_set = {i.key for i in instruments}
        prev_ts: datetime | None = None
        for rec in self._records():
            if rec.get("type") != "tick":
                continue
            tick = self._parse_tick(rec)
            if tick.instrument.key not in sym_set:
                continue
            await self._maybe_sleep(prev_ts, tick.timestamp)
            prev_ts = tick.timestamp
            yield tick

    async def stream_bars(
        self,
        instruments: Sequence[Instrument],
        timeframe: BarTimeframe,
    ) -> AsyncIterator[Bar]:
        if not self._connected:
            raise RuntimeError("FixtureAdapter not connected — call connect() first")
        sym_set = {i.key for i in instruments}
        prev_ts: datetime | None = None
        for rec in self._records():
            if rec.get("type") != "bar":
                continue
            if rec.get("timeframe") != timeframe.value:
                continue
            bar = self._parse_bar(rec)
            if bar.instrument.key not in sym_set:
                continue
            await self._maybe_sleep(prev_ts, bar.timestamp)
            prev_ts = bar.timestamp
            yield bar
