"""Directional / trend strength: ADX, DMI."""

from __future__ import annotations

import math
from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.registry import register
from features.series import RollingSeries, true_range


class ADX(Indicator):
    """Welles Wilder's ADX + DMI (+DI / -DI)."""

    name = "adx"
    version = "1"
    output_keys = ("adx", "plus_di", "minus_di")

    def __init__(self, period: int = 14) -> None:
        super().__init__()
        if period < 2:
            raise ValueError("adx period must be >= 2")
        self._period = period
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self._prev_close: float | None = None
        # Wilder-smoothed accumulators (RMA equivalents) for TR, +DM, -DM.
        self._smoothed_tr: float | None = None
        self._smoothed_plus_dm: float | None = None
        self._smoothed_minus_dm: float | None = None
        # For the initial averages we accumulate a full window of raw TR/+DM/-DM.
        self._init_tr: RollingSeries = RollingSeries(period)
        self._init_plus_dm: RollingSeries = RollingSeries(period)
        self._init_minus_dm: RollingSeries = RollingSeries(period)
        # ADX is a Wilder average of DX over `period`.
        self._dx_init: RollingSeries = RollingSeries(period)
        self._adx: float | None = None
        # ADX is ready after 2*period (rough) bars: one window of TR for DI, then
        # another window of DX to smooth into ADX.
        self.warmup = 2 * period + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        h = float(bar.high)
        l = float(bar.low)
        c = float(bar.close)

        if (
            self._prev_high is None
            or self._prev_low is None
            or self._prev_close is None
        ):
            self._prev_high, self._prev_low, self._prev_close = h, l, c
            return {"adx": math.nan, "plus_di": math.nan, "minus_di": math.nan}

        up_move = h - self._prev_high
        down_move = self._prev_low - l
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0
        tr = true_range(h, l, self._prev_close)

        self._prev_high, self._prev_low, self._prev_close = h, l, c

        # Phase 1: collect first `period` values, then bootstrap with simple sum.
        if self._smoothed_tr is None:
            self._init_tr.push(tr)
            self._init_plus_dm.push(plus_dm)
            self._init_minus_dm.push(minus_dm)
            if not self._init_tr.full:
                return {"adx": math.nan, "plus_di": math.nan, "minus_di": math.nan}
            self._smoothed_tr = self._init_tr.sum()
            self._smoothed_plus_dm = self._init_plus_dm.sum()
            self._smoothed_minus_dm = self._init_minus_dm.sum()
        else:
            assert self._smoothed_plus_dm is not None
            assert self._smoothed_minus_dm is not None
            self._smoothed_tr = self._smoothed_tr - self._smoothed_tr / self._period + tr
            self._smoothed_plus_dm = (
                self._smoothed_plus_dm - self._smoothed_plus_dm / self._period + plus_dm
            )
            self._smoothed_minus_dm = (
                self._smoothed_minus_dm - self._smoothed_minus_dm / self._period + minus_dm
            )

        if self._smoothed_tr == 0:
            return {"adx": math.nan, "plus_di": math.nan, "minus_di": math.nan}

        plus_di = 100.0 * self._smoothed_plus_dm / self._smoothed_tr
        minus_di = 100.0 * self._smoothed_minus_dm / self._smoothed_tr
        denom = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0

        # Phase 2: Wilder-smooth DX into ADX.
        if self._adx is None:
            self._dx_init.push(dx)
            if not self._dx_init.full:
                return {"adx": math.nan, "plus_di": plus_di, "minus_di": minus_di}
            self._adx = self._dx_init.mean()
        else:
            self._adx = (self._adx * (self._period - 1) + dx) / self._period

        return {"adx": self._adx, "plus_di": plus_di, "minus_di": minus_di}

    def reset(self) -> None:
        super().reset()
        self._prev_high = self._prev_low = self._prev_close = None
        self._smoothed_tr = None
        self._smoothed_plus_dm = None
        self._smoothed_minus_dm = None
        self._init_tr.reset()
        self._init_plus_dm.reset()
        self._init_minus_dm.reset()
        self._dx_init.reset()
        self._adx = None


@register("adx")
def _make_adx(period: int = 14) -> Indicator:
    return ADX(period=period)
