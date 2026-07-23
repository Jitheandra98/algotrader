"""Relative strength — cross-instrument return ranking.

Unlike single-instrument :class:`Indicator`, this is a multi-instrument calculator.
Strategies feed every instrument they care about and then ask for per-instrument
ranks vs a configured benchmark or peer group.

Two modes:
  - **vs benchmark**: per-instrument return minus the benchmark's return over the
    same window. Useful for momentum filtering ("only buy stocks outperforming
    SPY").
  - **cross-sectional rank**: O'Neil-style 1-99 percentile across a peer group.
    Optionally combined across multiple lookbacks with configurable weights.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from core.schemas import Bar, Instrument


@dataclass(frozen=True, slots=True)
class RSReading:
    instrument: Instrument
    score: float  # 0-100 if ranked; otherwise raw return delta
    return_pct: float
    benchmark_return_pct: float | None = None
    rank: int | None = None  # 1 = best, N = worst (when ranked)


class RelativeStrength:
    """Per-instrument rolling returns + cross-sectional ranking.

    Args:
        lookbacks: list of bar-count lookbacks (e.g., [63, 126, 252] for ~3m/6m/12m
          on daily bars).
        weights: weights for combining lookbacks. Must sum to 1.0 and match
          ``lookbacks``. Default: equal weights.
        benchmark: optional instrument key to use as the benchmark for delta-RS.
          When set, ``score`` returns the weighted return minus benchmark.
        rank: if True, ``score`` is the 0-100 percentile rank within the universe.
          Cannot be combined with ``benchmark`` (mutually exclusive modes).
    """

    def __init__(
        self,
        lookbacks: Iterable[int] = (63, 126, 252),
        weights: Iterable[float] | None = None,
        *,
        benchmark: str | None = None,
        rank: bool = False,
    ) -> None:
        lookbacks_list = list(lookbacks)
        if not lookbacks_list:
            raise ValueError("at least one lookback required")
        if any(lb < 2 for lb in lookbacks_list):
            raise ValueError("lookbacks must be >= 2")
        weights_list = list(weights) if weights is not None else [1.0 / len(lookbacks_list)] * len(lookbacks_list)
        if len(weights_list) != len(lookbacks_list):
            raise ValueError("weights length must match lookbacks length")
        if abs(sum(weights_list) - 1.0) > 1e-9:
            raise ValueError("weights must sum to 1.0")
        if benchmark and rank:
            raise ValueError("benchmark and rank are mutually exclusive")
        self._lookbacks = lookbacks_list
        self._weights = weights_list
        self._max_lb = max(lookbacks_list)
        self._benchmark = benchmark
        self._rank = rank
        # Per-instrument close history.
        self._closes: dict[str, deque[float]] = {}
        # Universe ordering preserved for stable rank output.
        self._instruments: dict[str, Instrument] = {}

    def feed(self, bar: Bar) -> None:
        """Record a bar's close for the bar's instrument."""
        key = bar.instrument.key
        buf = self._closes.get(key)
        if buf is None:
            buf = deque(maxlen=self._max_lb + 1)
            self._closes[key] = buf
            self._instruments[key] = bar.instrument
        buf.append(float(bar.close))

    def _weighted_return(self, key: str) -> float | None:
        buf = self._closes.get(key)
        if buf is None or len(buf) < self._max_lb + 1:
            return None
        closes = list(buf)
        weighted = 0.0
        for lb, w in zip(self._lookbacks, self._weights):
            prior = closes[-lb - 1]
            current = closes[-1]
            if prior <= 0:
                return None
            ret = (current / prior) - 1.0
            weighted += w * ret
        return weighted

    def reading(self, instrument: Instrument | str) -> RSReading | None:
        key = instrument if isinstance(instrument, str) else instrument.key
        inst = self._instruments.get(key)
        if inst is None:
            return None
        wr = self._weighted_return(key)
        if wr is None:
            return None

        if self._benchmark:
            br = self._weighted_return(self._benchmark)
            if br is None:
                return None
            return RSReading(
                instrument=inst,
                score=wr - br,
                return_pct=wr,
                benchmark_return_pct=br,
            )

        if self._rank:
            scores: list[tuple[str, float]] = []
            for k in self._instruments:
                r = self._weighted_return(k)
                if r is not None:
                    scores.append((k, r))
            if not scores:
                return None
            scores.sort(key=lambda x: x[1], reverse=True)  # best first
            n = len(scores)
            for i, (k, _r) in enumerate(scores):
                if k == key:
                    # Convert rank → 0-100 percentile (1 → 99, N → 1 like IBD).
                    if n == 1:
                        score = 99.0
                    else:
                        score = 99.0 - (i * 98.0 / (n - 1))
                    return RSReading(
                        instrument=inst,
                        score=score,
                        return_pct=wr,
                        rank=i + 1,
                    )
            return None

        # No mode selected — return raw weighted return as score.
        return RSReading(instrument=inst, score=wr, return_pct=wr)

    def all_readings(self) -> dict[str, RSReading]:
        out: dict[str, RSReading] = {}
        for key in self._instruments:
            r = self.reading(key)
            if r is not None:
                out[key] = r
        return out

    def reset(self) -> None:
        self._closes.clear()
        self._instruments.clear()
