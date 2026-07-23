"""Bar aggregators — transform ticks (or bars) into other bar shapes.

All aggregators implement :class:`BarAggregator`. They are intentionally synchronous
because the per-tick path is hot; async dispatch is the caller's job.

Implementations:
  - :class:`TimeBarAggregator`      — 1s, 5s, 1m, …, 1d wall-clock bars
  - :class:`HeikinAshiAggregator`   — derived from upstream time bars
  - :class:`RenkoAggregator`        — fixed-brick-size price bars
  - :class:`RangeBarAggregator`     — close when H-L exceeds threshold
"""

from data.aggregators.base import AggregatorInput, BarAggregator
from data.aggregators.heikin_ashi import HeikinAshiAggregator
from data.aggregators.range_bars import RangeBarAggregator
from data.aggregators.renko import RenkoAggregator
from data.aggregators.time_bars import TimeBarAggregator

__all__ = [
    "AggregatorInput",
    "BarAggregator",
    "HeikinAshiAggregator",
    "RangeBarAggregator",
    "RenkoAggregator",
    "TimeBarAggregator",
]
