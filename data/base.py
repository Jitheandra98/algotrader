"""``DataAdapter`` — the contract every data source implements.

The contract is intentionally narrow: adapters yield ticks or bars as async iterators,
and provide a historical-bars query. Everything else (auth, rate limiting, reconnect)
is the adapter's problem and hidden from the consumer.

This keeps the rest of the system broker-agnostic. Switching from Alpaca to IBKR to
CCXT is a configuration change — same downstream code path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from core.schemas import Bar, BarTimeframe, Instrument, Tick


class FeedMode(str, Enum):
    """How the adapter is configured to source live data."""

    TICKS = "TICKS"   # subscribe to L1 quotes/trades
    BARS = "BARS"     # subscribe to broker-aggregated bars
    BOTH = "BOTH"     # both streams interleaved


@dataclass(frozen=True, slots=True)
class HistoricalBarRequest:
    """Parameters for a historical bar fetch. Inclusive on ``start``, exclusive on
    ``end`` — same convention as Alpaca and IBKR."""

    instruments: Sequence[Instrument]
    timeframe: BarTimeframe
    start: datetime
    end: datetime
    limit: int | None = None


class DataAdapter(ABC):
    """Broker / venue / fixture data source."""

    name: str

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def historical_bars(self, request: HistoricalBarRequest) -> AsyncIterator[Bar]:
        """Yield historical bars in chronological order, paginating as needed.

        Implementations should handle pagination, rate limiting, and retries
        internally and yield bars to the caller as they arrive.
        """
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]

    @abstractmethod
    async def stream_ticks(self, instruments: Sequence[Instrument]) -> AsyncIterator[Tick]:
        """Subscribe to live ticks (L1 quotes + trades). Reconnects internally."""
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]

    @abstractmethod
    async def stream_bars(
        self,
        instruments: Sequence[Instrument],
        timeframe: BarTimeframe,
    ) -> AsyncIterator[Bar]:
        """Subscribe to broker-aggregated bars at ``timeframe``. Some venues offer
        this directly (Alpaca, IBKR); for those that don't, the implementation may
        synthesize from ticks via the local aggregator."""
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]
