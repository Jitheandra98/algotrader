"""Moving averages.

Implements: SMA, EMA, DEMA, TEMA, WMA, HMA, ALMA, LSMA, VWMA.

All take an optional ``source`` parameter for which bar price field to use
(close, open, hl2, hlc3, ohlc4). EMA-family seeds with SMA over the first period
to match TradingView and TA-Lib conventions.
"""

from __future__ import annotations

import math
from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.indicators._sources import Source, select
from features.registry import register
from features.series import RollingSeries


# ---------------------------------------------------------------- SMA


class SMA(Indicator):
    name = "sma"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20, source: Source = "close") -> None:
        super().__init__()
        if period <= 0:
            raise ValueError("period must be positive")
        self._period = period
        self._source = source
        self._series = RollingSeries(period)
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._series.push(select(bar, self._source))
        return {"value": self._series.mean()}

    def reset(self) -> None:
        super().reset()
        self._series.reset()


@register("sma")
def _make_sma(period: int = 20, source: Source = "close") -> Indicator:
    return SMA(period=period, source=source)


# ---------------------------------------------------------------- EMA


class EMA(Indicator):
    """SMA-seeded EMA. ``alpha = 2/(N+1)``."""

    name = "ema"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20, source: Source = "close") -> None:
        super().__init__()
        if period <= 0:
            raise ValueError("period must be positive")
        self._period = period
        self._source = source
        self._alpha = 2.0 / (period + 1)
        self._seed = RollingSeries(period)
        self._ema: float | None = None
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        price = select(bar, self._source)
        if self._ema is None:
            self._seed.push(price)
            if self._seed.full:
                self._ema = self._seed.mean()
            # Not ready yet; report best-effort SMA value but warmup gate is what
            # downstream consumers check.
            return {"value": self._seed.mean()}
        self._ema = self._alpha * price + (1 - self._alpha) * self._ema
        return {"value": self._ema}

    def current(self) -> float | None:
        """Helper for composite indicators that want the raw EMA without a Bar."""
        return self._ema

    def feed_value(self, value: float) -> float | None:
        """Composite-friendly path: drive the EMA from a non-bar source (e.g. another
        EMA's output, for DEMA/TEMA). Returns the new EMA value or None during seed."""
        if self._ema is None:
            self._seed.push(value)
            if self._seed.full:
                self._ema = self._seed.mean()
                self._count = self._period
                return self._ema
            self._count += 1
            return None
        self._ema = self._alpha * value + (1 - self._alpha) * self._ema
        self._count += 1
        return self._ema

    def reset(self) -> None:
        super().reset()
        self._seed.reset()
        self._ema = None


@register("ema")
def _make_ema(period: int = 20, source: Source = "close") -> Indicator:
    return EMA(period=period, source=source)


# ---------------------------------------------------------------- DEMA / TEMA


class DEMA(Indicator):
    """Double EMA: 2·EMA - EMA(EMA)."""

    name = "dema"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20, source: Source = "close") -> None:
        super().__init__()
        self._period = period
        self._source = source
        self._ema1 = EMA(period=period, source=source)
        self._ema2 = EMA(period=period, source=source)
        # Ready when EMA(EMA) has both seeds filled: 2*period - 1
        self.warmup = 2 * period - 1

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        r1 = self._ema1.update(bar)
        v1 = r1.values.get("value")
        if v1 is None:
            return {"value": math.nan}
        v2 = self._ema2.feed_value(v1)
        if v2 is None:
            return {"value": math.nan}
        return {"value": 2 * v1 - v2}

    def reset(self) -> None:
        super().reset()
        self._ema1.reset()
        self._ema2.reset()


@register("dema")
def _make_dema(period: int = 20, source: Source = "close") -> Indicator:
    return DEMA(period=period, source=source)


class TEMA(Indicator):
    """Triple EMA: 3·EMA - 3·EMA(EMA) + EMA(EMA(EMA))."""

    name = "tema"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20, source: Source = "close") -> None:
        super().__init__()
        self._period = period
        self._source = source
        self._ema1 = EMA(period=period, source=source)
        self._ema2 = EMA(period=period, source=source)
        self._ema3 = EMA(period=period, source=source)
        # Three-stage seed: 3*period - 2
        self.warmup = 3 * period - 2

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        r1 = self._ema1.update(bar)
        v1 = r1.values.get("value")
        if v1 is None:
            return {"value": math.nan}
        v2 = self._ema2.feed_value(v1)
        if v2 is None:
            return {"value": math.nan}
        v3 = self._ema3.feed_value(v2)
        if v3 is None:
            return {"value": math.nan}
        return {"value": 3 * v1 - 3 * v2 + v3}

    def reset(self) -> None:
        super().reset()
        self._ema1.reset()
        self._ema2.reset()
        self._ema3.reset()


@register("tema")
def _make_tema(period: int = 20, source: Source = "close") -> Indicator:
    return TEMA(period=period, source=source)


# ---------------------------------------------------------------- WMA


class WMA(Indicator):
    """Linear-weighted MA: weights = 1..N, denominator = N(N+1)/2."""

    name = "wma"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20, source: Source = "close") -> None:
        super().__init__()
        if period <= 0:
            raise ValueError("period must be positive")
        self._period = period
        self._source = source
        self._series = RollingSeries(period)
        self._denom = period * (period + 1) / 2.0
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._series.push(select(bar, self._source))
        return {"value": self._wma()}

    def _wma(self) -> float:
        values = self._series.values()
        n = len(values)
        if n == 0:
            return math.nan
        num = sum(v * (i + 1) for i, v in enumerate(values))
        denom = n * (n + 1) / 2.0
        return num / denom

    def reset(self) -> None:
        super().reset()
        self._series.reset()


@register("wma")
def _make_wma(period: int = 20, source: Source = "close") -> Indicator:
    return WMA(period=period, source=source)


# ---------------------------------------------------------------- HMA


class HMA(Indicator):
    """Hull MA: ``WMA(2·WMA(N/2) - WMA(N), sqrt(N))``.

    Fast & smooth; gives less lag than EMA.
    """

    name = "hma"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 16, source: Source = "close") -> None:
        super().__init__()
        if period <= 1:
            raise ValueError("hma period must be > 1")
        self._period = period
        self._source = source
        self._n_half = max(1, period // 2)
        self._n_sqrt = max(1, int(math.sqrt(period)))
        self._inner_short = WMA(period=self._n_half, source=source)
        self._inner_long = WMA(period=period, source=source)
        self._outer = WMA(period=self._n_sqrt, source=source)
        # Inner WMAs need ``period`` bars; outer then needs ``sqrt(N)``.
        self.warmup = period + self._n_sqrt - 1

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        r_short = self._inner_short.update(bar)
        r_long = self._inner_long.update(bar)
        if not (r_short.ready and r_long.ready):
            return {"value": math.nan}
        raw = 2 * r_short.values["value"] - r_long.values["value"]
        # Feed the raw composite into the outer WMA. We need to inject as a value;
        # since WMA expects bars, build a synthetic bar's close. Simpler: use a
        # plain rolling list to do the outer WMA inline.
        return {"value": self._outer_feed(raw, bar)}

    def __init_outer_buf(self) -> None:
        self._outer_buf: list[float] = []

    def _outer_feed(self, value: float, bar: Bar) -> float:
        if not hasattr(self, "_outer_buf"):
            self.__init_outer_buf()
        self._outer_buf.append(value)
        if len(self._outer_buf) > self._n_sqrt:
            self._outer_buf.pop(0)
        n = len(self._outer_buf)
        denom = n * (n + 1) / 2.0
        num = sum(v * (i + 1) for i, v in enumerate(self._outer_buf))
        return num / denom

    def reset(self) -> None:
        super().reset()
        self._inner_short.reset()
        self._inner_long.reset()
        if hasattr(self, "_outer_buf"):
            self._outer_buf.clear()


@register("hma")
def _make_hma(period: int = 16, source: Source = "close") -> Indicator:
    return HMA(period=period, source=source)


# ---------------------------------------------------------------- ALMA


class ALMA(Indicator):
    """Arnaud Legoux MA. Gaussian weights centered at ``offset*(N-1)`` with width
    controlled by ``sigma``. Smoother & less laggy than SMA/EMA.

    Defaults: offset=0.85, sigma=6, period=9 (Legoux's original).
    """

    name = "alma"
    version = "1"
    output_keys = ("value",)

    def __init__(
        self,
        period: int = 9,
        offset: float = 0.85,
        sigma: float = 6.0,
        source: Source = "close",
    ) -> None:
        super().__init__()
        if period <= 0:
            raise ValueError("period must be positive")
        if not 0.0 <= offset <= 1.0:
            raise ValueError("offset must be in [0,1]")
        if sigma <= 0:
            raise ValueError("sigma must be positive")
        self._period = period
        self._offset = offset
        self._sigma = sigma
        self._source = source
        self._series = RollingSeries(period)
        # Precompute weights.
        m = offset * (period - 1)
        s = period / sigma
        weights = [math.exp(-((i - m) ** 2) / (2 * s * s)) for i in range(period)]
        wsum = sum(weights)
        self._weights = [w / wsum for w in weights]
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {
            "period": self._period,
            "offset": self._offset,
            "sigma": self._sigma,
            "source": self._source,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._series.push(select(bar, self._source))
        values = self._series.values()
        if len(values) < self._period:
            return {"value": math.nan}
        return {"value": sum(v * w for v, w in zip(values, self._weights))}

    def reset(self) -> None:
        super().reset()
        self._series.reset()


@register("alma")
def _make_alma(
    period: int = 9, offset: float = 0.85, sigma: float = 6.0, source: Source = "close"
) -> Indicator:
    return ALMA(period=period, offset=offset, sigma=sigma, source=source)


# ---------------------------------------------------------------- LSMA


class LSMA(Indicator):
    """Least-Squares MA (a.k.a. linear regression value): the endpoint of the OLS
    regression line over the last N values. Slope-sensitive, more responsive than
    SMA but more noisy.
    """

    name = "lsma"
    version = "1"
    output_keys = ("value", "slope")

    def __init__(self, period: int = 14, source: Source = "close") -> None:
        super().__init__()
        if period < 2:
            raise ValueError("lsma period must be >= 2")
        self._period = period
        self._source = source
        self._series = RollingSeries(period)
        # Precompute x-related sums for OLS: x ranges 0..N-1.
        n = period
        self._sum_x = sum(range(n))
        self._sum_x2 = sum(i * i for i in range(n))
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._series.push(select(bar, self._source))
        values = self._series.values()
        n = len(values)
        if n < self._period:
            return {"value": math.nan, "slope": math.nan}
        sum_y = sum(values)
        sum_xy = sum(i * y for i, y in enumerate(values))
        denom = n * self._sum_x2 - self._sum_x * self._sum_x
        slope = (n * sum_xy - self._sum_x * sum_y) / denom
        intercept = (sum_y - slope * self._sum_x) / n
        endpoint = intercept + slope * (n - 1)
        return {"value": endpoint, "slope": slope}

    def reset(self) -> None:
        super().reset()
        self._series.reset()


@register("lsma")
def _make_lsma(period: int = 14, source: Source = "close") -> Indicator:
    return LSMA(period=period, source=source)


# ---------------------------------------------------------------- VWMA


class VWMA(Indicator):
    """Volume-weighted MA over a rolling window."""

    name = "vwma"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20, source: Source = "close") -> None:
        super().__init__()
        if period <= 0:
            raise ValueError("period must be positive")
        self._period = period
        self._source = source
        self._prices: RollingSeries = RollingSeries(period)
        self._volumes: RollingSeries = RollingSeries(period)
        self._pv: RollingSeries = RollingSeries(period)
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        price = select(bar, self._source)
        volume = float(bar.volume)
        self._prices.push(price)
        self._volumes.push(volume)
        self._pv.push(price * volume)
        vsum = self._volumes.sum()
        if vsum == 0:
            return {"value": math.nan}
        return {"value": self._pv.sum() / vsum}

    def reset(self) -> None:
        super().reset()
        self._prices.reset()
        self._volumes.reset()
        self._pv.reset()


@register("vwma")
def _make_vwma(period: int = 20, source: Source = "close") -> Indicator:
    return VWMA(period=period, source=source)
