"""Band indicators: Bollinger, Keltner, Donchian, percentage Envelope."""

from __future__ import annotations

import math
from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.indicators._sources import Source, select
from features.indicators.moving_averages import EMA, SMA
from features.registry import register
from features.series import RollingSeries, true_range


# ---------------------------------------------------------------- Bollinger


class BollingerBands(Indicator):
    """SMA ± k·σ. Standard k=2, period=20."""

    name = "bbands"
    version = "1"
    output_keys = ("middle", "upper", "lower", "width", "percent_b")

    def __init__(
        self, period: int = 20, k: float = 2.0, source: Source = "close"
    ) -> None:
        super().__init__()
        if period < 2:
            raise ValueError("bbands period must be >= 2")
        if k <= 0:
            raise ValueError("bbands k must be positive")
        self._period = period
        self._k = k
        self._source = source
        self._series = RollingSeries(period)
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "k": self._k, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        price = select(bar, self._source)
        self._series.push(price)
        if not self._series.full:
            return {
                "middle": math.nan,
                "upper": math.nan,
                "lower": math.nan,
                "width": math.nan,
                "percent_b": math.nan,
            }
        mid = self._series.mean()
        std = self._series.stdev(ddof=0)
        upper = mid + self._k * std
        lower = mid - self._k * std
        width = (upper - lower) / mid if mid != 0 else math.nan
        # Percent B: position within bands.
        if upper == lower:
            percent_b = 0.5
        else:
            percent_b = (price - lower) / (upper - lower)
        return {
            "middle": mid,
            "upper": upper,
            "lower": lower,
            "width": width,
            "percent_b": percent_b,
        }

    def reset(self) -> None:
        super().reset()
        self._series.reset()


@register("bbands")
def _make_bbands(period: int = 20, k: float = 2.0, source: Source = "close") -> Indicator:
    return BollingerBands(period=period, k=k, source=source)


# ---------------------------------------------------------------- Keltner


class KeltnerChannels(Indicator):
    """EMA ± k·ATR. Defaults: period=20, atr_period=10, k=2."""

    name = "keltner"
    version = "1"
    output_keys = ("middle", "upper", "lower")

    def __init__(
        self,
        period: int = 20,
        atr_period: int = 10,
        k: float = 2.0,
        source: Source = "close",
    ) -> None:
        super().__init__()
        if period < 2 or atr_period < 1 or k <= 0:
            raise ValueError("invalid keltner params")
        self._period = period
        self._atr_period = atr_period
        self._k = k
        self._source = source
        self._ema = EMA(period=period, source=source)
        self._tr: RollingSeries = RollingSeries(atr_period)
        self._atr: float | None = None
        self._prev_close: float | None = None
        self.warmup = max(period, atr_period + 1)

    @property
    def params(self) -> dict[str, Any]:
        return {
            "period": self._period,
            "atr_period": self._atr_period,
            "k": self._k,
            "source": self._source,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        h, l, c = float(bar.high), float(bar.low), float(bar.close)
        tr = true_range(h, l, self._prev_close)
        self._prev_close = c

        if self._atr is None:
            self._tr.push(tr)
            if self._tr.full:
                self._atr = self._tr.mean()
        else:
            self._atr = (self._atr * (self._atr_period - 1) + tr) / self._atr_period

        r = self._ema.update(bar)
        if not r.ready or self._atr is None:
            return {"middle": math.nan, "upper": math.nan, "lower": math.nan}
        mid = r.values["value"]
        return {
            "middle": mid,
            "upper": mid + self._k * self._atr,
            "lower": mid - self._k * self._atr,
        }

    def reset(self) -> None:
        super().reset()
        self._ema.reset()
        self._tr.reset()
        self._atr = None
        self._prev_close = None


@register("keltner")
def _make_keltner(
    period: int = 20, atr_period: int = 10, k: float = 2.0, source: Source = "close"
) -> Indicator:
    return KeltnerChannels(period=period, atr_period=atr_period, k=k, source=source)


# ---------------------------------------------------------------- Donchian


class DonchianChannels(Indicator):
    """Highest high / lowest low / midline over N bars."""

    name = "donchian"
    version = "1"
    output_keys = ("upper", "lower", "middle")

    def __init__(self, period: int = 20) -> None:
        super().__init__()
        if period < 1:
            raise ValueError("donchian period must be >= 1")
        self._period = period
        self._highs = RollingSeries(period)
        self._lows = RollingSeries(period)
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._highs.push(float(bar.high))
        self._lows.push(float(bar.low))
        if not self._highs.full:
            return {"upper": math.nan, "lower": math.nan, "middle": math.nan}
        upper = self._highs.max()
        lower = self._lows.min()
        return {"upper": upper, "lower": lower, "middle": (upper + lower) / 2.0}

    def reset(self) -> None:
        super().reset()
        self._highs.reset()
        self._lows.reset()


@register("donchian")
def _make_donchian(period: int = 20) -> Indicator:
    return DonchianChannels(period=period)


# ---------------------------------------------------------------- Envelope


class Envelope(Indicator):
    """SMA ± p%. Simple percentage envelope around an MA."""

    name = "envelope"
    version = "1"
    output_keys = ("middle", "upper", "lower")

    def __init__(self, period: int = 20, percent: float = 2.5, source: Source = "close") -> None:
        super().__init__()
        if period < 1 or percent <= 0:
            raise ValueError("invalid envelope params")
        self._period = period
        self._percent = percent
        self._source = source
        self._sma = SMA(period=period, source=source)
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {
            "period": self._period,
            "percent": self._percent,
            "source": self._source,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        r = self._sma.update(bar)
        if not r.ready:
            return {"middle": math.nan, "upper": math.nan, "lower": math.nan}
        mid = r.values["value"]
        margin = mid * self._percent / 100.0
        return {"middle": mid, "upper": mid + margin, "lower": mid - margin}

    def reset(self) -> None:
        super().reset()
        self._sma.reset()


@register("envelope")
def _make_envelope(
    period: int = 20, percent: float = 2.5, source: Source = "close"
) -> Indicator:
    return Envelope(period=period, percent=percent, source=source)
