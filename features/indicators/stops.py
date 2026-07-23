"""Stop-loss indicators: ATR, SuperTrend, Parabolic SAR, Chandelier Exit, Volatility Stop."""

from __future__ import annotations

import math
from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.registry import register
from features.series import RollingSeries, true_range


# ---------------------------------------------------------------- ATR


class ATR(Indicator):
    """Average True Range (Wilder)."""

    name = "atr"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 14) -> None:
        super().__init__()
        if period < 1:
            raise ValueError("atr period must be >= 1")
        self._period = period
        self._tr: RollingSeries = RollingSeries(period)
        self._atr: float | None = None
        self._prev_close: float | None = None
        self.warmup = period + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        h, l, c = float(bar.high), float(bar.low), float(bar.close)
        tr = true_range(h, l, self._prev_close)
        self._prev_close = c
        if self._atr is None:
            self._tr.push(tr)
            if self._tr.full:
                self._atr = self._tr.mean()
                return {"value": self._atr}
            return {"value": math.nan}
        self._atr = (self._atr * (self._period - 1) + tr) / self._period
        return {"value": self._atr}

    def reset(self) -> None:
        super().reset()
        self._tr.reset()
        self._atr = None
        self._prev_close = None


@register("atr")
def _make_atr(period: int = 14) -> Indicator:
    return ATR(period=period)


# ---------------------------------------------------------------- SuperTrend


class SuperTrend(Indicator):
    """SuperTrend: hl2 ± factor·ATR with band-locking. +1 = long, -1 = short."""

    name = "supertrend"
    version = "1"
    output_keys = ("value", "trend")

    def __init__(self, period: int = 10, factor: float = 3.0) -> None:
        super().__init__()
        if period < 1 or factor <= 0:
            raise ValueError("invalid supertrend params")
        self._period = period
        self._factor = factor
        self._atr = ATR(period=period)
        self._prev_close: float | None = None
        self._final_upper: float | None = None
        self._final_lower: float | None = None
        self._trend: int = 1
        self.warmup = period + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "factor": self._factor}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        h, l, c = float(bar.high), float(bar.low), float(bar.close)
        r = self._atr.update(bar)
        atr = r.values.get("value")
        if atr is None or math.isnan(atr) or not r.ready:
            self._prev_close = c
            return {"value": math.nan, "trend": 0.0}

        mid = (h + l) / 2.0
        basic_upper = mid + self._factor * atr
        basic_lower = mid - self._factor * atr

        # Band-locking against previous final bands.
        if self._final_upper is None or self._prev_close is None or self._prev_close > self._final_upper:
            final_upper = basic_upper
        else:
            final_upper = min(basic_upper, self._final_upper)

        if self._final_lower is None or self._prev_close is None or self._prev_close < self._final_lower:
            final_lower = basic_lower
        else:
            final_lower = max(basic_lower, self._final_lower)

        # Trend update.
        if self._final_upper is not None and c > self._final_upper:
            self._trend = 1
        elif self._final_lower is not None and c < self._final_lower:
            self._trend = -1
        # else: hold previous trend.

        self._final_upper = final_upper
        self._final_lower = final_lower
        self._prev_close = c

        value = final_lower if self._trend == 1 else final_upper
        return {"value": value, "trend": float(self._trend)}

    def reset(self) -> None:
        super().reset()
        self._atr.reset()
        self._prev_close = None
        self._final_upper = None
        self._final_lower = None
        self._trend = 1


@register("supertrend")
def _make_supertrend(period: int = 10, factor: float = 3.0) -> Indicator:
    return SuperTrend(period=period, factor=factor)


# ---------------------------------------------------------------- Parabolic SAR


class ParabolicSAR(Indicator):
    """Parabolic SAR (Wilder)."""

    name = "psar"
    version = "1"
    output_keys = ("value", "trend")

    def __init__(self, af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2) -> None:
        super().__init__()
        if not (0 < af_start <= af_max) or af_step <= 0:
            raise ValueError("invalid psar params")
        self._af_start = af_start
        self._af_step = af_step
        self._af_max = af_max
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._sar: float | None = None
        self._ep: float | None = None  # extreme point
        self._af: float = af_start
        self._trend: int = 0  # +1 = up, -1 = down
        self.warmup = 2

    @property
    def params(self) -> dict[str, Any]:
        return {"af_start": self._af_start, "af_step": self._af_step, "af_max": self._af_max}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        h, l = float(bar.high), float(bar.low)

        if self._prev_high is None or self._prev_low is None:
            # First bar: stash for the next iteration's direction decision.
            self._prev_high, self._prev_low = h, l
            return {"value": math.nan, "trend": 0.0}

        if self._trend == 0:
            # Decide initial trend on bar 2.
            if h >= self._prev_high:
                self._trend = 1
                self._sar = self._prev_low
                self._ep = h
            else:
                self._trend = -1
                self._sar = self._prev_high
                self._ep = l
            self._af = self._af_start
            self._prev_high, self._prev_low = h, l
            return {"value": self._sar, "trend": float(self._trend)}

        assert self._sar is not None

        assert self._ep is not None and self._prev_high is not None and self._prev_low is not None
        sar = self._sar + self._af * (self._ep - self._sar)

        if self._trend == 1:
            # SAR can't be above the previous two lows.
            sar = min(sar, self._prev_low, l)
            reversed_ = l < sar
        else:
            sar = max(sar, self._prev_high, h)
            reversed_ = h > sar

        if reversed_:
            new_trend = -self._trend
            # Switch: SAR becomes the EP, EP resets to current extreme, AF resets.
            self._sar = self._ep
            self._ep = l if new_trend == -1 else h
            self._af = self._af_start
            self._trend = new_trend
        else:
            self._sar = sar
            if self._trend == 1 and h > self._ep:
                self._ep = h
                self._af = min(self._af + self._af_step, self._af_max)
            elif self._trend == -1 and l < self._ep:
                self._ep = l
                self._af = min(self._af + self._af_step, self._af_max)

        self._prev_high, self._prev_low = h, l
        return {"value": self._sar, "trend": float(self._trend)}

    def reset(self) -> None:
        super().reset()
        self._prev_high = self._prev_low = None
        self._sar = None
        self._ep = None
        self._af = self._af_start
        self._trend = 0


@register("psar")
def _make_psar(af_start: float = 0.02, af_step: float = 0.02, af_max: float = 0.2) -> Indicator:
    return ParabolicSAR(af_start=af_start, af_step=af_step, af_max=af_max)


# ---------------------------------------------------------------- Chandelier Exit


class ChandelierExit(Indicator):
    """Chandelier Exit. Long exit = highest_high - factor·ATR.
    Short exit = lowest_low + factor·ATR."""

    name = "chandelier"
    version = "1"
    output_keys = ("long_exit", "short_exit")

    def __init__(self, period: int = 22, atr_period: int = 22, factor: float = 3.0) -> None:
        super().__init__()
        if period < 1 or atr_period < 1 or factor <= 0:
            raise ValueError("invalid chandelier params")
        self._period = period
        self._atr_period = atr_period
        self._factor = factor
        self._highs: RollingSeries = RollingSeries(period)
        self._lows: RollingSeries = RollingSeries(period)
        self._atr = ATR(period=atr_period)
        self.warmup = max(period, atr_period + 1)

    @property
    def params(self) -> dict[str, Any]:
        return {
            "period": self._period,
            "atr_period": self._atr_period,
            "factor": self._factor,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._highs.push(float(bar.high))
        self._lows.push(float(bar.low))
        r = self._atr.update(bar)
        atr = r.values.get("value")
        if atr is None or math.isnan(atr) or not self._highs.full:
            return {"long_exit": math.nan, "short_exit": math.nan}
        return {
            "long_exit": self._highs.max() - self._factor * atr,
            "short_exit": self._lows.min() + self._factor * atr,
        }

    def reset(self) -> None:
        super().reset()
        self._highs.reset()
        self._lows.reset()
        self._atr.reset()


@register("chandelier")
def _make_chandelier(
    period: int = 22, atr_period: int = 22, factor: float = 3.0
) -> Indicator:
    return ChandelierExit(period=period, atr_period=atr_period, factor=factor)


# ---------------------------------------------------------------- Volatility Stop


class VolatilityStop(Indicator):
    """Welles Wilder's Volatility Stop: trailing stop based on close ± factor·ATR.

    Latches against the most extreme close in the current trend until close crosses
    the previous stop, at which point trend flips.
    """

    name = "volstop"
    version = "1"
    output_keys = ("value", "trend")

    def __init__(self, atr_period: int = 7, factor: float = 3.0) -> None:
        super().__init__()
        if atr_period < 1 or factor <= 0:
            raise ValueError("invalid volstop params")
        self._atr = ATR(period=atr_period)
        self._factor = factor
        self._stop: float | None = None
        self._trend: int = 0
        self._best_close: float | None = None
        self.warmup = atr_period + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"atr_period": self._atr._period, "factor": self._factor}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        c = float(bar.close)
        r = self._atr.update(bar)
        atr = r.values.get("value")
        if atr is None or math.isnan(atr) or not r.ready:
            return {"value": math.nan, "trend": 0.0}

        if self._stop is None:
            # Initialize trend on first ready ATR.
            self._trend = 1
            self._best_close = c
            self._stop = c - self._factor * atr
            return {"value": self._stop, "trend": float(self._trend)}

        assert self._best_close is not None
        if self._trend == 1:
            if c < self._stop:
                self._trend = -1
                self._best_close = c
                self._stop = c + self._factor * atr
            else:
                if c > self._best_close:
                    self._best_close = c
                self._stop = max(self._stop, self._best_close - self._factor * atr)
        else:  # trend == -1
            if c > self._stop:
                self._trend = 1
                self._best_close = c
                self._stop = c - self._factor * atr
            else:
                if c < self._best_close:
                    self._best_close = c
                self._stop = min(self._stop, self._best_close + self._factor * atr)

        return {"value": self._stop, "trend": float(self._trend)}

    def reset(self) -> None:
        super().reset()
        self._atr.reset()
        self._stop = None
        self._trend = 0
        self._best_close = None


@register("volstop")
def _make_volstop(atr_period: int = 7, factor: float = 3.0) -> Indicator:
    return VolatilityStop(atr_period=atr_period, factor=factor)
