"""Rolling-window helpers shared by many indicators.

Most indicators need a bounded-history view of the last N values. ``RollingSeries``
backs that with a ``collections.deque`` plus a running sum so SMA-like operations
are O(1) per update.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable


class RollingSeries:
    """Fixed-length FIFO with O(1) sum maintenance.

    Use when you need the last N items AND a fast sum/mean. For min/max use the
    convenience methods (which are O(N) for now — fine for the period sizes we use).
    """

    __slots__ = ("_buf", "_period", "_sum")

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise ValueError("period must be positive")
        self._period = period
        self._buf: deque[float] = deque(maxlen=period)
        self._sum: float = 0.0

    def __len__(self) -> int:
        return len(self._buf)

    def __iter__(self) -> Iterable[float]:
        return iter(self._buf)

    @property
    def period(self) -> int:
        return self._period

    @property
    def full(self) -> bool:
        return len(self._buf) == self._period

    def push(self, value: float) -> None:
        if len(self._buf) == self._period:
            self._sum -= self._buf[0]
        self._buf.append(value)
        self._sum += value

    def sum(self) -> float:
        return self._sum

    def mean(self) -> float:
        n = len(self._buf)
        return self._sum / n if n > 0 else math.nan

    def stdev(self, *, ddof: int = 0) -> float:
        """Population stdev by default (ddof=0). Pass ddof=1 for sample."""
        n = len(self._buf)
        if n <= ddof:
            return math.nan
        m = self._sum / n
        sq = sum((x - m) ** 2 for x in self._buf)
        return math.sqrt(sq / (n - ddof))

    def min(self) -> float:
        return min(self._buf) if self._buf else math.nan

    def max(self) -> float:
        return max(self._buf) if self._buf else math.nan

    def last(self) -> float:
        return self._buf[-1] if self._buf else math.nan

    def first(self) -> float:
        return self._buf[0] if self._buf else math.nan

    def values(self) -> list[float]:
        return list(self._buf)

    def reset(self) -> None:
        self._buf.clear()
        self._sum = 0.0


def true_range(high: float, low: float, prev_close: float | None) -> float:
    """Welles Wilder's True Range: max of (H-L, |H-Cprev|, |L-Cprev|).

    On the first bar (no ``prev_close``) it degenerates to H-L.
    """
    rng = high - low
    if prev_close is None:
        return rng
    return max(rng, abs(high - prev_close), abs(low - prev_close))


def wilder_smooth(prev: float, new: float, period: int) -> float:
    """Welles Wilder's recursive smoothing: ``prev - prev/N + new``.

    Equivalent to an EMA with alpha = 1/N. Used by RSI, ATR, ADX.
    """
    return prev - (prev / period) + new
