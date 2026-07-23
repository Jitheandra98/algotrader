"""Swing-point detection used by chart-pattern detectors.

A bar is a *confirmed swing high* if its high is the maximum of all highs in a
window of ``window`` bars before AND ``window`` bars after it. The same logic with
``low`` defines a *confirmed swing low*. Because we need ``window`` bars of
lookahead, swings emit with that delay.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from core.schemas import Bar


SwingKind = Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class Swing:
    kind: SwingKind
    timestamp: datetime
    price: float


class SwingDetector:
    """Sliding-window confirmed-swing detector. Per-instrument."""

    __slots__ = ("_window", "_buf", "_emitted")

    def __init__(self, window: int = 3) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self._window = window
        self._buf: deque[Bar] = deque(maxlen=2 * window + 1)
        self._emitted: list[Swing] = []

    @property
    def window(self) -> int:
        return self._window

    @property
    def confirmed(self) -> list[Swing]:
        return list(self._emitted)

    def push(self, bar: Bar) -> list[Swing]:
        """Append a bar and return any swings confirmed at this step."""
        self._buf.append(bar)
        out: list[Swing] = []
        if len(self._buf) < 2 * self._window + 1:
            return out
        center = self._buf[self._window]
        highs = [float(b.high) for b in self._buf]
        lows = [float(b.low) for b in self._buf]
        # Strictly greater than all peers — equality breaks symmetry oddly.
        center_high = float(center.high)
        center_low = float(center.low)
        if center_high == max(highs) and highs.count(center_high) == 1:
            sw = Swing(kind="high", timestamp=center.timestamp, price=center_high)
            self._emitted.append(sw)
            out.append(sw)
        if center_low == min(lows) and lows.count(center_low) == 1:
            sw = Swing(kind="low", timestamp=center.timestamp, price=center_low)
            self._emitted.append(sw)
            out.append(sw)
        return out

    def reset(self) -> None:
        self._buf.clear()
        self._emitted.clear()
