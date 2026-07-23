"""Walk-forward analysis — partition bars into (in-sample, out-of-sample) windows.

Two modes:
  - **Rolling**: fixed-size IS and OOS windows that slide forward.
  - **Anchored**: IS grows monotonically; OOS slides forward each step.

The runner doesn't optimize on its own — that's the caller's job. It just yields
window pairs the caller can feed to two separate ``BacktestEngine`` runs (one to
fit, one to score).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")  # typically Bar but kept generic so we can window equity points too


@dataclass(frozen=True, slots=True)
class WalkForwardWindow(Generic[T]):
    index: int          # 0-based window number
    in_sample: list[T]
    out_of_sample: list[T]


class WalkForwardRunner(Generic[T]):
    """Yields :class:`WalkForwardWindow` slices over a sorted sequence."""

    def __init__(
        self,
        *,
        is_size: int,
        oos_size: int,
        step: int | None = None,
        anchored: bool = False,
    ) -> None:
        if is_size < 2 or oos_size < 1:
            raise ValueError("is_size >= 2 and oos_size >= 1 required")
        if step is not None and step < 1:
            raise ValueError("step must be >= 1")
        self._is = is_size
        self._oos = oos_size
        self._step = step or oos_size  # default: non-overlapping OOS
        self._anchored = anchored

    def windows(self, data: Sequence[T]) -> Iterator[WalkForwardWindow[T]]:
        n = len(data)
        if n < self._is + self._oos:
            return
        idx = 0
        start = 0
        while True:
            if self._anchored:
                is_start = 0
                is_end = self._is + idx * self._step
            else:
                is_start = start
                is_end = start + self._is
            oos_start = is_end
            oos_end = oos_start + self._oos
            if oos_end > n:
                break
            yield WalkForwardWindow(
                index=idx,
                in_sample=list(data[is_start:is_end]),
                out_of_sample=list(data[oos_start:oos_end]),
            )
            idx += 1
            start += self._step
