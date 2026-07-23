"""Linear regression channel: slope, intercept, endpoint, R², std-dev bands."""

from __future__ import annotations

import math
from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.indicators._sources import Source, select
from features.registry import register
from features.series import RollingSeries


class LinearRegression(Indicator):
    """OLS over the last N values of ``source``. Returns the regression endpoint
    (predicted value at x=N-1), the slope, R², and optional ±k·σ bands derived from
    the residuals.
    """

    name = "linreg"
    version = "1"
    output_keys = ("value", "slope", "r_squared", "upper", "lower")

    def __init__(
        self,
        period: int = 20,
        k_bands: float = 2.0,
        source: Source = "close",
    ) -> None:
        super().__init__()
        if period < 3:
            raise ValueError("linreg period must be >= 3")
        if k_bands <= 0:
            raise ValueError("k_bands must be positive")
        self._period = period
        self._k = k_bands
        self._source = source
        self._series: RollingSeries = RollingSeries(period)
        n = period
        self._sum_x = sum(range(n))
        self._sum_x2 = sum(i * i for i in range(n))
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "k_bands": self._k, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._series.push(select(bar, self._source))
        if not self._series.full:
            return {
                "value": math.nan,
                "slope": math.nan,
                "r_squared": math.nan,
                "upper": math.nan,
                "lower": math.nan,
            }
        values = self._series.values()
        n = self._period
        sum_y = sum(values)
        sum_xy = sum(i * y for i, y in enumerate(values))
        sum_y2 = sum(y * y for y in values)
        denom = n * self._sum_x2 - self._sum_x * self._sum_x
        slope = (n * sum_xy - self._sum_x * sum_y) / denom
        intercept = (sum_y - slope * self._sum_x) / n
        endpoint = intercept + slope * (n - 1)

        # R²
        mean_y = sum_y / n
        ss_total = sum_y2 - n * mean_y * mean_y
        ss_residual = sum(
            (y - (intercept + slope * i)) ** 2 for i, y in enumerate(values)
        )
        r2 = 1.0 - ss_residual / ss_total if ss_total > 0 else 0.0

        # Std-dev bands from residual std.
        residual_std = math.sqrt(ss_residual / n) if n > 0 else math.nan
        upper = endpoint + self._k * residual_std
        lower = endpoint - self._k * residual_std
        return {
            "value": endpoint,
            "slope": slope,
            "r_squared": r2,
            "upper": upper,
            "lower": lower,
        }

    def reset(self) -> None:
        super().reset()
        self._series.reset()


@register("linreg")
def _make_linreg(
    period: int = 20, k_bands: float = 2.0, source: Source = "close"
) -> Indicator:
    return LinearRegression(period=period, k_bands=k_bands, source=source)
