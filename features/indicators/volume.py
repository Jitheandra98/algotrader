"""Volume indicators: VWAP (session + anchored), OBV, A/D Line, RVOL,
Volume-by-Price / Point of Control.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timezone
from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.registry import register
from features.series import RollingSeries

UTC = timezone.utc


# ---------------------------------------------------------------- VWAP


class VWAP(Indicator):
    """Volume-Weighted Average Price.

    Two modes:
      - ``anchor=None`` (default): session VWAP. Resets at ``session_start_utc`` each
        UTC day. For US equities use ``time(13, 30)`` (9:30 ET in UTC offset).
      - ``anchor=<datetime>``: anchored VWAP that starts at that exact timestamp
        and never resets.
    """

    name = "vwap"
    version = "1"
    output_keys = ("value",)

    def __init__(
        self,
        *,
        anchor: datetime | None = None,
        session_start_utc: time = time(0, 0),
    ) -> None:
        super().__init__()
        self._anchor = anchor
        self._session_start = session_start_utc
        self._cum_pv: float = 0.0
        self._cum_v: float = 0.0
        self._last_session_date: datetime | None = None
        self.warmup = 1  # one bar after the anchor / session start

    @property
    def params(self) -> dict[str, Any]:
        return {
            "anchor": self._anchor.isoformat() if self._anchor else None,
            "session_start_utc": self._session_start.isoformat(),
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        ts = bar.timestamp
        # If anchored and bar is before the anchor, skip.
        if self._anchor is not None and ts < self._anchor:
            return {"value": math.nan}

        # Session VWAP: detect crossing into a new session and reset.
        if self._anchor is None:
            # The session-start datetime that "owns" this bar. A bar before today's
            # session_start belongs to yesterday's session that started 24h earlier.
            session_open_today = datetime.combine(ts.date(), self._session_start, tzinfo=UTC)
            if ts < session_open_today:
                from datetime import timedelta
                session_open = session_open_today - timedelta(days=1)
            else:
                session_open = session_open_today
            if self._last_session_date != session_open:
                self._cum_pv = 0.0
                self._cum_v = 0.0
                self._last_session_date = session_open

        tp = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
        v = float(bar.volume)
        self._cum_pv += tp * v
        self._cum_v += v
        if self._cum_v == 0:
            return {"value": tp}
        return {"value": self._cum_pv / self._cum_v}

    def reset(self) -> None:
        super().reset()
        self._cum_pv = 0.0
        self._cum_v = 0.0
        self._last_session_date = None


@register("vwap")
def _make_vwap(
    anchor: datetime | None = None, session_start_utc: time = time(0, 0)
) -> Indicator:
    return VWAP(anchor=anchor, session_start_utc=session_start_utc)


# ---------------------------------------------------------------- OBV


class OBV(Indicator):
    """On-Balance Volume."""

    name = "obv"
    version = "1"
    output_keys = ("value",)

    def __init__(self) -> None:
        super().__init__()
        self._obv: float = 0.0
        self._prev_close: float | None = None
        self.warmup = 2  # need a previous close

    def _update(self, bar: Bar) -> dict[str, float] | None:
        c = float(bar.close)
        v = float(bar.volume)
        if self._prev_close is not None:
            if c > self._prev_close:
                self._obv += v
            elif c < self._prev_close:
                self._obv -= v
        self._prev_close = c
        return {"value": self._obv}

    def reset(self) -> None:
        super().reset()
        self._obv = 0.0
        self._prev_close = None


@register("obv")
def _make_obv() -> Indicator:
    return OBV()


# ---------------------------------------------------------------- A/D Line


class ADLine(Indicator):
    """Accumulation / Distribution line (Chaikin)."""

    name = "adline"
    version = "1"
    output_keys = ("value",)

    def __init__(self) -> None:
        super().__init__()
        self._ad: float = 0.0
        self.warmup = 1

    def _update(self, bar: Bar) -> dict[str, float] | None:
        h, l, c = float(bar.high), float(bar.low), float(bar.close)
        v = float(bar.volume)
        if h == l:
            mfm = 0.0
        else:
            mfm = ((c - l) - (h - c)) / (h - l)
        self._ad += mfm * v
        return {"value": self._ad}

    def reset(self) -> None:
        super().reset()
        self._ad = 0.0


@register("adline")
def _make_adline() -> Indicator:
    return ADLine()


# ---------------------------------------------------------------- RVOL


class RVOL(Indicator):
    """Relative Volume: current volume / mean volume over previous N bars (rolling).

    Note: a more sophisticated 'same-time-of-day' RVOL requires intraday session
    bucketing — added in M9 when we have session calendars.
    """

    name = "rvol"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20) -> None:
        super().__init__()
        if period < 1:
            raise ValueError("rvol period must be >= 1")
        self._period = period
        self._series: RollingSeries = RollingSeries(period)
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        v = float(bar.volume)
        if not self._series.full:
            self._series.push(v)
            return {"value": math.nan}
        avg = self._series.mean()
        # Push AFTER reading the mean so the current bar isn't in the denominator.
        self._series.push(v)
        if avg == 0:
            return {"value": math.nan}
        return {"value": v / avg}

    def reset(self) -> None:
        super().reset()
        self._series.reset()


@register("rvol")
def _make_rvol(period: int = 20) -> Indicator:
    return RVOL(period=period)


# ---------------------------------------------------------------- Volume-by-Price / POC


class VolumeByPrice(Indicator):
    """Rolling-window Volume Profile.

    Allocates each bar's volume into ``bins`` equal-width price buckets spanning the
    rolling window's full range. Emits the bucket bounds + the **Point of Control**
    (POC), i.e. the bucket centre with the highest volume.

    Designed for use with weekly/daily bars over rolling windows of dozens to
    hundreds of bars. For minute-bar volume profiles see the dedicated
    ``SessionVolumeProfile`` (M9).
    """

    name = "vbp"
    version = "1"
    output_keys = ("poc", "value_area_low", "value_area_high")

    def __init__(self, period: int = 50, bins: int = 24, value_area_pct: float = 0.7) -> None:
        super().__init__()
        if period < 2:
            raise ValueError("vbp period must be >= 2")
        if bins < 2:
            raise ValueError("vbp bins must be >= 2")
        if not 0.0 < value_area_pct <= 1.0:
            raise ValueError("value_area_pct must be in (0,1]")
        self._period = period
        self._bins = bins
        self._value_area = value_area_pct
        self._bars: list[Bar] = []
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {
            "period": self._period,
            "bins": self._bins,
            "value_area_pct": self._value_area,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._bars.append(bar)
        if len(self._bars) > self._period:
            self._bars.pop(0)
        if len(self._bars) < self._period:
            return {
                "poc": math.nan,
                "value_area_low": math.nan,
                "value_area_high": math.nan,
            }
        lo = min(float(b.low) for b in self._bars)
        hi = max(float(b.high) for b in self._bars)
        if hi == lo:
            return {"poc": lo, "value_area_low": lo, "value_area_high": hi}
        step = (hi - lo) / self._bins
        buckets = [0.0] * self._bins
        for b in self._bars:
            # Distribute the bar's volume evenly across its price range buckets.
            blo = (float(b.low) - lo) / step
            bhi = (float(b.high) - lo) / step
            i_lo = max(0, int(blo))
            i_hi = min(self._bins - 1, int(bhi))
            span = max(1, i_hi - i_lo + 1)
            share = float(b.volume) / span
            for i in range(i_lo, i_hi + 1):
                buckets[i] += share

        poc_idx = max(range(self._bins), key=lambda i: buckets[i])
        poc = lo + (poc_idx + 0.5) * step

        # Value area: expand around POC until the contained volume ≥ value_area_pct.
        total_v = sum(buckets)
        target = total_v * self._value_area
        lo_i, hi_i = poc_idx, poc_idx
        contained = buckets[poc_idx]
        while contained < target and (lo_i > 0 or hi_i < self._bins - 1):
            left = buckets[lo_i - 1] if lo_i > 0 else -1.0
            right = buckets[hi_i + 1] if hi_i < self._bins - 1 else -1.0
            if left >= right and lo_i > 0:
                lo_i -= 1
                contained += buckets[lo_i]
            elif hi_i < self._bins - 1:
                hi_i += 1
                contained += buckets[hi_i]
            else:
                break
        return {
            "poc": poc,
            "value_area_low": lo + lo_i * step,
            "value_area_high": lo + (hi_i + 1) * step,
        }

    def reset(self) -> None:
        super().reset()
        self._bars.clear()


@register("vbp")
def _make_vbp(period: int = 50, bins: int = 24, value_area_pct: float = 0.7) -> Indicator:
    return VolumeByPrice(period=period, bins=bins, value_area_pct=value_area_pct)
