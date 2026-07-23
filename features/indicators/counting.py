"""Counting / sequencing indicators: TD Sequential (DeMark), TheSTRAT bar types.

These are not classical math indicators — they're rule-based bar classifiers used
to identify reversal points and continuation patterns.
"""

from __future__ import annotations

import math
from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.registry import register


# ---------------------------------------------------------------- TD Sequential


class TDSequential(Indicator):
    """Simplified TD Sequential (DeMark).

    Tracks the *setup* phase (1..9):
      - **Buy setup**: 9 consecutive closes each less than the close 4 bars earlier.
      - **Sell setup**: 9 consecutive closes each greater than the close 4 bars earlier.

    Emits ``setup_buy`` and ``setup_sell`` counts. Countdown phase (after 9) is
    omitted in this implementation — easy to add later if needed.
    """

    name = "tdseq"
    version = "1"
    output_keys = ("setup_buy", "setup_sell")

    def __init__(self) -> None:
        super().__init__()
        self._closes: list[float] = []
        self._buy_count: int = 0
        self._sell_count: int = 0
        self.warmup = 5  # need close[i] and close[i-4]

    def _update(self, bar: Bar) -> dict[str, float] | None:
        c = float(bar.close)
        self._closes.append(c)
        if len(self._closes) > 64:
            # Bound memory; we only need [i-4] so 16 is enough but keep a safety margin.
            self._closes = self._closes[-32:]
        if len(self._closes) < 5:
            return {"setup_buy": 0.0, "setup_sell": 0.0}
        c4 = self._closes[-5]
        if c < c4:
            self._buy_count = min(9, self._buy_count + 1)
            self._sell_count = 0
        elif c > c4:
            self._sell_count = min(9, self._sell_count + 1)
            self._buy_count = 0
        else:
            # No progression on equals; reset both like most implementations.
            self._buy_count = 0
            self._sell_count = 0
        return {"setup_buy": float(self._buy_count), "setup_sell": float(self._sell_count)}

    def reset(self) -> None:
        super().reset()
        self._closes.clear()
        self._buy_count = 0
        self._sell_count = 0


@register("tdseq")
def _make_tdseq() -> Indicator:
    return TDSequential()


# ---------------------------------------------------------------- TheSTRAT


class StratBarType(Indicator):
    """Rob Smith's STRAT classifier. Per-bar:

      - **1**  (inside): H <= prev H AND L >= prev L
      - **2u** (one-side up): H > prev H AND L >= prev L
      - **2d** (one-side down): L < prev L AND H <= prev H
      - **3**  (outside): H > prev H AND L < prev L

    Emits ``code`` as a float:
      1.0 = inside, 2.1 = 2u, 2.2 = 2d, 3.0 = outside, 0.0 = first bar.
    """

    name = "strat"
    version = "1"
    output_keys = ("code", "is_inside", "is_two_up", "is_two_down", "is_outside")

    def __init__(self) -> None:
        super().__init__()
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self.warmup = 2

    def _update(self, bar: Bar) -> dict[str, float] | None:
        h, l = float(bar.high), float(bar.low)
        if self._prev_high is None or self._prev_low is None:
            self._prev_high, self._prev_low = h, l
            return {
                "code": 0.0,
                "is_inside": 0.0,
                "is_two_up": 0.0,
                "is_two_down": 0.0,
                "is_outside": 0.0,
            }
        higher_high = h > self._prev_high
        lower_low = l < self._prev_low
        is_inside = (h <= self._prev_high) and (l >= self._prev_low)
        is_outside = higher_high and lower_low
        is_two_up = higher_high and (l >= self._prev_low) and not is_outside
        is_two_down = lower_low and (h <= self._prev_high) and not is_outside

        if is_inside:
            code = 1.0
        elif is_outside:
            code = 3.0
        elif is_two_up:
            code = 2.1
        elif is_two_down:
            code = 2.2
        else:
            code = math.nan  # ambiguous; shouldn't happen

        self._prev_high, self._prev_low = h, l
        return {
            "code": code,
            "is_inside": float(is_inside),
            "is_two_up": float(is_two_up),
            "is_two_down": float(is_two_down),
            "is_outside": float(is_outside),
        }

    def reset(self) -> None:
        super().reset()
        self._prev_high = self._prev_low = None


@register("strat")
def _make_strat() -> Indicator:
    return StratBarType()
