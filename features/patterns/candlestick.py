"""Candlestick pattern detectors.

Each detector implements :class:`Indicator`. Outputs are 0.0 / 1.0 'detected' flags
under the field ``signal``. Multi-bar patterns track their own state.

Conventions:
  - Body = |open - close|
  - Upper wick = high - max(open, close)
  - Lower wick = min(open, close) - low
  - 'Long body' means body >= ``long_body_factor`` × candle range (default 0.6)
  - 'Small body' means body <= ``small_body_factor`` × candle range (default 0.3)
"""

from __future__ import annotations

from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.registry import register


# ---- helpers -----------------------------------------------------------


def _candle_metrics(bar: Bar) -> tuple[float, float, float, float, float]:
    """Return (body, upper_wick, lower_wick, range, mid_body)."""
    o, h, l, c = float(bar.open), float(bar.high), float(bar.low), float(bar.close)
    body = abs(o - c)
    rng = h - l
    upper = h - max(o, c)
    lower = min(o, c) - l
    mid = (o + c) / 2.0
    return body, upper, lower, rng, mid


def _is_bullish(bar: Bar) -> bool:
    return float(bar.close) > float(bar.open)


def _is_bearish(bar: Bar) -> bool:
    return float(bar.close) < float(bar.open)


# ---- 1-bar patterns -----------------------------------------------------


class Doji(Indicator):
    """Doji: body is a small fraction of the bar's range. ``threshold`` default 5%."""

    name = "pattern.doji"
    version = "1"
    output_keys = ("signal",)

    def __init__(self, threshold: float = 0.05) -> None:
        super().__init__()
        if not 0 < threshold < 1:
            raise ValueError("threshold must be in (0,1)")
        self._t = threshold
        self.warmup = 1

    @property
    def params(self) -> dict[str, Any]:
        return {"threshold": self._t}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        body, _u, _l, rng, _m = _candle_metrics(bar)
        signal = 1.0 if rng > 0 and (body / rng) <= self._t else 0.0
        return {"signal": signal}


@register("pattern.doji")
def _make_doji(threshold: float = 0.05) -> Indicator:
    return Doji(threshold=threshold)


class Hammer(Indicator):
    """Hammer / hanging-man shape: small body near top, long lower wick.

    The pattern says nothing about *trend context* — that's the caller's job (a
    hammer in a downtrend is bullish; the same shape in an uptrend is a hanging
    man). Strategies layer the trend filter on top.
    """

    name = "pattern.hammer"
    version = "1"
    output_keys = ("signal",)

    def __init__(
        self,
        body_max_pct: float = 0.3,
        lower_wick_min_factor: float = 2.0,
        upper_wick_max_pct: float = 0.1,
    ) -> None:
        super().__init__()
        self._body_max = body_max_pct
        self._lwf = lower_wick_min_factor
        self._uwp = upper_wick_max_pct
        self.warmup = 1

    @property
    def params(self) -> dict[str, Any]:
        return {
            "body_max_pct": self._body_max,
            "lower_wick_min_factor": self._lwf,
            "upper_wick_max_pct": self._uwp,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        body, upper, lower, rng, _m = _candle_metrics(bar)
        if rng <= 0:
            return {"signal": 0.0}
        small_body = body <= self._body_max * rng
        long_lower = body > 0 and lower >= self._lwf * body or (body == 0 and lower >= 0.6 * rng)
        small_upper = upper <= self._uwp * rng
        return {"signal": 1.0 if small_body and long_lower and small_upper else 0.0}


@register("pattern.hammer")
def _make_hammer(
    body_max_pct: float = 0.3,
    lower_wick_min_factor: float = 2.0,
    upper_wick_max_pct: float = 0.1,
) -> Indicator:
    return Hammer(
        body_max_pct=body_max_pct,
        lower_wick_min_factor=lower_wick_min_factor,
        upper_wick_max_pct=upper_wick_max_pct,
    )


class InvertedHammer(Indicator):
    """Inverted-hammer / shooting-star shape: small body near bottom, long upper wick."""

    name = "pattern.inverted_hammer"
    version = "1"
    output_keys = ("signal",)

    def __init__(
        self,
        body_max_pct: float = 0.3,
        upper_wick_min_factor: float = 2.0,
        lower_wick_max_pct: float = 0.1,
    ) -> None:
        super().__init__()
        self._body_max = body_max_pct
        self._uwf = upper_wick_min_factor
        self._lwp = lower_wick_max_pct
        self.warmup = 1

    @property
    def params(self) -> dict[str, Any]:
        return {
            "body_max_pct": self._body_max,
            "upper_wick_min_factor": self._uwf,
            "lower_wick_max_pct": self._lwp,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        body, upper, lower, rng, _m = _candle_metrics(bar)
        if rng <= 0:
            return {"signal": 0.0}
        small_body = body <= self._body_max * rng
        long_upper = body > 0 and upper >= self._uwf * body or (body == 0 and upper >= 0.6 * rng)
        small_lower = lower <= self._lwp * rng
        return {"signal": 1.0 if small_body and long_upper and small_lower else 0.0}


@register("pattern.inverted_hammer")
def _make_inverted_hammer(
    body_max_pct: float = 0.3,
    upper_wick_min_factor: float = 2.0,
    lower_wick_max_pct: float = 0.1,
) -> Indicator:
    return InvertedHammer(
        body_max_pct=body_max_pct,
        upper_wick_min_factor=upper_wick_min_factor,
        lower_wick_max_pct=lower_wick_max_pct,
    )


# ---- 2-bar patterns: engulfing -----------------------------------------


class BullishEngulfing(Indicator):
    """Prev bar bearish; current bar bullish; current body fully covers prev body."""

    name = "pattern.engulfing_bull"
    version = "1"
    output_keys = ("signal",)

    def __init__(self) -> None:
        super().__init__()
        self._prev: Bar | None = None
        self.warmup = 2

    def _update(self, bar: Bar) -> dict[str, float] | None:
        signal = 0.0
        if self._prev is not None:
            if _is_bearish(self._prev) and _is_bullish(bar):
                prev_top = max(float(self._prev.open), float(self._prev.close))
                prev_bot = min(float(self._prev.open), float(self._prev.close))
                if float(bar.open) <= prev_bot and float(bar.close) >= prev_top:
                    signal = 1.0
        self._prev = bar
        return {"signal": signal}

    def reset(self) -> None:
        super().reset()
        self._prev = None


@register("pattern.engulfing_bull")
def _make_engulfing_bull() -> Indicator:
    return BullishEngulfing()


class BearishEngulfing(Indicator):
    """Prev bar bullish; current bar bearish; current body fully covers prev body."""

    name = "pattern.engulfing_bear"
    version = "1"
    output_keys = ("signal",)

    def __init__(self) -> None:
        super().__init__()
        self._prev: Bar | None = None
        self.warmup = 2

    def _update(self, bar: Bar) -> dict[str, float] | None:
        signal = 0.0
        if self._prev is not None:
            if _is_bullish(self._prev) and _is_bearish(bar):
                prev_top = max(float(self._prev.open), float(self._prev.close))
                prev_bot = min(float(self._prev.open), float(self._prev.close))
                if float(bar.open) >= prev_top and float(bar.close) <= prev_bot:
                    signal = 1.0
        self._prev = bar
        return {"signal": signal}

    def reset(self) -> None:
        super().reset()
        self._prev = None


@register("pattern.engulfing_bear")
def _make_engulfing_bear() -> Indicator:
    return BearishEngulfing()


# ---- 3-bar patterns: three soldiers / crows ----------------------------


class ThreeWhiteSoldiers(Indicator):
    """3 consecutive bullish bars, each opening within the previous body and
    closing higher than the previous close."""

    name = "pattern.three_white_soldiers"
    version = "1"
    output_keys = ("signal",)

    def __init__(self) -> None:
        super().__init__()
        self._bars: list[Bar] = []
        self.warmup = 3

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._bars.append(bar)
        if len(self._bars) > 3:
            self._bars.pop(0)
        if len(self._bars) < 3:
            return {"signal": 0.0}
        a, b, c = self._bars
        all_bullish = _is_bullish(a) and _is_bullish(b) and _is_bullish(c)
        rising_closes = float(a.close) < float(b.close) < float(c.close)
        opens_within = (
            float(a.open) <= float(b.open) <= float(a.close)
            and float(b.open) <= float(c.open) <= float(b.close)
        )
        return {"signal": 1.0 if all_bullish and rising_closes and opens_within else 0.0}

    def reset(self) -> None:
        super().reset()
        self._bars.clear()


@register("pattern.three_white_soldiers")
def _make_3ws() -> Indicator:
    return ThreeWhiteSoldiers()


class ThreeBlackCrows(Indicator):
    """3 consecutive bearish bars, opens within prev body, closes lower."""

    name = "pattern.three_black_crows"
    version = "1"
    output_keys = ("signal",)

    def __init__(self) -> None:
        super().__init__()
        self._bars: list[Bar] = []
        self.warmup = 3

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._bars.append(bar)
        if len(self._bars) > 3:
            self._bars.pop(0)
        if len(self._bars) < 3:
            return {"signal": 0.0}
        a, b, c = self._bars
        all_bearish = _is_bearish(a) and _is_bearish(b) and _is_bearish(c)
        falling_closes = float(a.close) > float(b.close) > float(c.close)
        opens_within = (
            float(a.close) <= float(b.open) <= float(a.open)
            and float(b.close) <= float(c.open) <= float(b.open)
        )
        return {"signal": 1.0 if all_bearish and falling_closes and opens_within else 0.0}

    def reset(self) -> None:
        super().reset()
        self._bars.clear()


@register("pattern.three_black_crows")
def _make_3bc() -> Indicator:
    return ThreeBlackCrows()


# ---- multi-bar: mat hold, stick sandwich -------------------------------


class MatHold(Indicator):
    """Bullish Mat Hold continuation (5 bars):

    1. Long bullish bar
    2-4. Three small bars, each closing below previous (consolidation)
    5. Long bullish bar that closes above the bar 1 high.
    """

    name = "pattern.mat_hold"
    version = "1"
    output_keys = ("signal",)

    def __init__(self) -> None:
        super().__init__()
        self._bars: list[Bar] = []
        self.warmup = 5

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._bars.append(bar)
        if len(self._bars) > 5:
            self._bars.pop(0)
        if len(self._bars) < 5:
            return {"signal": 0.0}
        b1, b2, b3, b4, b5 = self._bars
        body1, *_ = _candle_metrics(b1)
        body5, *_ = _candle_metrics(b5)
        small_2_3_4 = all(
            _candle_metrics(b)[0] < body1 / 2.0 for b in (b2, b3, b4)
        )
        consolidation_down = (
            float(b2.close) > float(b3.close) > float(b4.close)
        )
        bullish_breakout = (
            _is_bullish(b1)
            and _is_bullish(b5)
            and body5 >= body1 * 0.8
            and float(b5.close) > float(b1.high)
        )
        return {
            "signal": 1.0
            if small_2_3_4 and consolidation_down and bullish_breakout
            else 0.0
        }

    def reset(self) -> None:
        super().reset()
        self._bars.clear()


@register("pattern.mat_hold")
def _make_mat_hold() -> Indicator:
    return MatHold()


class StickSandwich(Indicator):
    """Stick sandwich: bearish (close X), bullish, bearish closing at close X."""

    name = "pattern.stick_sandwich"
    version = "1"
    output_keys = ("signal",)

    def __init__(self, tolerance_pct: float = 0.001) -> None:
        super().__init__()
        self._tol = tolerance_pct
        self._bars: list[Bar] = []
        self.warmup = 3

    @property
    def params(self) -> dict[str, Any]:
        return {"tolerance_pct": self._tol}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._bars.append(bar)
        if len(self._bars) > 3:
            self._bars.pop(0)
        if len(self._bars) < 3:
            return {"signal": 0.0}
        a, b, c = self._bars
        if not (_is_bearish(a) and _is_bullish(b) and _is_bearish(c)):
            return {"signal": 0.0}
        target = float(a.close)
        if target == 0:
            return {"signal": 0.0}
        match = abs(float(c.close) - target) / abs(target) <= self._tol
        # Middle candle's close must be above both outer closes (the "filling").
        middle_higher = float(b.close) > target
        return {"signal": 1.0 if match and middle_higher else 0.0}

    def reset(self) -> None:
        super().reset()
        self._bars.clear()


@register("pattern.stick_sandwich")
def _make_stick_sandwich(tolerance_pct: float = 0.001) -> Indicator:
    return StickSandwich(tolerance_pct=tolerance_pct)
