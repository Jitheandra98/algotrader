"""``BarAggregator`` — base class for all bar-shape transforms.

Two input modes:
  - Tick-driven (TimeBars, Renko, Range): ``on_tick(tick) -> list[Bar]``.
  - Bar-driven (HeikinAshi): ``on_bar(bar) -> list[Bar]``.

Aggregators that only work on one mode raise NotImplementedError on the other.
``flush()`` lets callers force-close an in-progress bar at session end / shutdown.

State is per-instrument: callers either run one aggregator per instrument or use the
:class:`MultiInstrumentAggregator` wrapper in ``data.aggregators.fanout`` (M3+).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeAlias

from core.schemas import Bar, Tick

AggregatorInput: TypeAlias = Tick | Bar


class BarAggregator(ABC):
    """Stateful transform from ticks/bars to bars."""

    @abstractmethod
    def on_tick(self, tick: Tick) -> list[Bar]:
        """Process a tick. Returns bars that *closed* on this tick (may be empty)."""

    @abstractmethod
    def on_bar(self, bar: Bar) -> list[Bar]:
        """Process an upstream bar. Returns derived bars (may be empty)."""

    @abstractmethod
    def flush(self) -> list[Bar]:
        """Force-close any in-progress bar. Returns 0 or 1 bars."""
