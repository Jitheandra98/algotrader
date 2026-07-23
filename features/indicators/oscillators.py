"""Momentum oscillators: RSI, MACD, Stochastic, Williams %R, CCI, Vortex, TRIX."""

from __future__ import annotations

import math
from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.indicators._sources import Source, select
from features.indicators.moving_averages import EMA, SMA
from features.registry import register
from features.series import RollingSeries, true_range


# ---------------------------------------------------------------- RSI


class RSI(Indicator):
    """Welles Wilder's RSI."""

    name = "rsi"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 14, source: Source = "close") -> None:
        super().__init__()
        if period < 2:
            raise ValueError("rsi period must be >= 2")
        self._period = period
        self._source = source
        self._prev: float | None = None
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None
        self._gains: RollingSeries = RollingSeries(period)
        self._losses: RollingSeries = RollingSeries(period)
        self.warmup = period + 1  # need one prior bar to compute the first diff

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        price = select(bar, self._source)
        if self._prev is None:
            self._prev = price
            return {"value": math.nan}

        change = price - self._prev
        self._prev = price
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if self._avg_gain is None:
            self._gains.push(gain)
            self._losses.push(loss)
            if self._gains.full:
                self._avg_gain = self._gains.mean()
                self._avg_loss = self._losses.mean()
                return {"value": self._rsi_from(self._avg_gain, self._avg_loss)}
            return {"value": math.nan}

        # Wilder smoothing.
        assert self._avg_loss is not None
        self._avg_gain = (self._avg_gain * (self._period - 1) + gain) / self._period
        self._avg_loss = (self._avg_loss * (self._period - 1) + loss) / self._period
        return {"value": self._rsi_from(self._avg_gain, self._avg_loss)}

    @staticmethod
    def _rsi_from(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    def reset(self) -> None:
        super().reset()
        self._prev = None
        self._avg_gain = None
        self._avg_loss = None
        self._gains.reset()
        self._losses.reset()


@register("rsi")
def _make_rsi(period: int = 14, source: Source = "close") -> Indicator:
    return RSI(period=period, source=source)


# ---------------------------------------------------------------- MACD


class MACD(Indicator):
    """MACD line, signal line, histogram."""

    name = "macd"
    version = "1"
    output_keys = ("macd", "signal", "hist")

    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        source: Source = "close",
    ) -> None:
        super().__init__()
        if fast <= 0 or slow <= 0 or signal <= 0:
            raise ValueError("macd periods must be positive")
        if fast >= slow:
            raise ValueError("fast must be < slow")
        self._fast = fast
        self._slow = slow
        self._signal = signal
        self._source = source
        self._ema_fast = EMA(period=fast, source=source)
        self._ema_slow = EMA(period=slow, source=source)
        self._signal_ema = EMA(period=signal, source=source)
        # Slow EMA ready after slow bars; then signal EMA needs another `signal` bars.
        self.warmup = slow + signal - 1

    @property
    def params(self) -> dict[str, Any]:
        return {
            "fast": self._fast,
            "slow": self._slow,
            "signal": self._signal,
            "source": self._source,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        r_fast = self._ema_fast.update(bar)
        r_slow = self._ema_slow.update(bar)
        if not (r_fast.ready and r_slow.ready):
            return {"macd": math.nan, "signal": math.nan, "hist": math.nan}
        macd_line = r_fast.values["value"] - r_slow.values["value"]
        sig = self._signal_ema.feed_value(macd_line)
        if sig is None:
            return {"macd": macd_line, "signal": math.nan, "hist": math.nan}
        return {"macd": macd_line, "signal": sig, "hist": macd_line - sig}

    def reset(self) -> None:
        super().reset()
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._signal_ema.reset()


@register("macd")
def _make_macd(
    fast: int = 12, slow: int = 26, signal: int = 9, source: Source = "close"
) -> Indicator:
    return MACD(fast=fast, slow=slow, signal=signal, source=source)


# ---------------------------------------------------------------- Stochastic


class Stochastic(Indicator):
    """Stochastic %K and %D (with optional %K smoothing)."""

    name = "stoch"
    version = "1"
    output_keys = ("k", "d")

    def __init__(self, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> None:
        super().__init__()
        if period < 1 or smooth_k < 1 or smooth_d < 1:
            raise ValueError("stoch periods must be >= 1")
        self._period = period
        self._smooth_k = smooth_k
        self._smooth_d = smooth_d
        self._highs: RollingSeries = RollingSeries(period)
        self._lows: RollingSeries = RollingSeries(period)
        self._k_raw: RollingSeries = RollingSeries(smooth_k)
        self._k_smoothed: RollingSeries = RollingSeries(smooth_d)
        self.warmup = period + smooth_k + smooth_d - 2

    @property
    def params(self) -> dict[str, Any]:
        return {
            "period": self._period,
            "smooth_k": self._smooth_k,
            "smooth_d": self._smooth_d,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._highs.push(float(bar.high))
        self._lows.push(float(bar.low))
        if not self._highs.full:
            return {"k": math.nan, "d": math.nan}
        h = self._highs.max()
        l = self._lows.min()
        if h == l:
            k_raw = 50.0
        else:
            k_raw = 100.0 * (float(bar.close) - l) / (h - l)
        self._k_raw.push(k_raw)
        if not self._k_raw.full:
            return {"k": math.nan, "d": math.nan}
        k = self._k_raw.mean()
        self._k_smoothed.push(k)
        if not self._k_smoothed.full:
            return {"k": k, "d": math.nan}
        return {"k": k, "d": self._k_smoothed.mean()}

    def reset(self) -> None:
        super().reset()
        self._highs.reset()
        self._lows.reset()
        self._k_raw.reset()
        self._k_smoothed.reset()


@register("stoch")
def _make_stoch(period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> Indicator:
    return Stochastic(period=period, smooth_k=smooth_k, smooth_d=smooth_d)


# ---------------------------------------------------------------- Williams %R


class WilliamsR(Indicator):
    """Williams %R: -100 * (high_n - close) / (high_n - low_n). Range [-100, 0]."""

    name = "willr"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 14) -> None:
        super().__init__()
        if period <= 0:
            raise ValueError("period must be positive")
        self._period = period
        self._highs: RollingSeries = RollingSeries(period)
        self._lows: RollingSeries = RollingSeries(period)
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        self._highs.push(float(bar.high))
        self._lows.push(float(bar.low))
        if not self._highs.full:
            return {"value": math.nan}
        h = self._highs.max()
        l = self._lows.min()
        if h == l:
            return {"value": -50.0}
        return {"value": -100.0 * (h - float(bar.close)) / (h - l)}

    def reset(self) -> None:
        super().reset()
        self._highs.reset()
        self._lows.reset()


@register("willr")
def _make_willr(period: int = 14) -> Indicator:
    return WilliamsR(period=period)


# ---------------------------------------------------------------- CCI


class CCI(Indicator):
    """Commodity Channel Index = (TP - SMA_TP) / (0.015 * mean_dev)."""

    name = "cci"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 20) -> None:
        super().__init__()
        if period <= 0:
            raise ValueError("period must be positive")
        self._period = period
        self._tp: RollingSeries = RollingSeries(period)
        self.warmup = period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        tp = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
        self._tp.push(tp)
        if not self._tp.full:
            return {"value": math.nan}
        mean_tp = self._tp.mean()
        mean_dev = sum(abs(v - mean_tp) for v in self._tp) / self._tp.period
        if mean_dev == 0:
            return {"value": 0.0}
        return {"value": (tp - mean_tp) / (0.015 * mean_dev)}

    def reset(self) -> None:
        super().reset()
        self._tp.reset()


@register("cci")
def _make_cci(period: int = 20) -> Indicator:
    return CCI(period=period)


# ---------------------------------------------------------------- Vortex


class Vortex(Indicator):
    """Vortex Indicator (VI+/VI-)."""

    name = "vortex"
    version = "1"
    output_keys = ("vi_plus", "vi_minus")

    def __init__(self, period: int = 14) -> None:
        super().__init__()
        if period < 2:
            raise ValueError("vortex period must be >= 2")
        self._period = period
        self._vm_plus: RollingSeries = RollingSeries(period)
        self._vm_minus: RollingSeries = RollingSeries(period)
        self._tr: RollingSeries = RollingSeries(period)
        self._prev_close: float | None = None
        self._prev_high: float | None = None
        self._prev_low: float | None = None
        self.warmup = period + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        h = float(bar.high)
        l = float(bar.low)
        c = float(bar.close)
        if self._prev_close is None or self._prev_high is None or self._prev_low is None:
            self._prev_close, self._prev_high, self._prev_low = c, h, l
            return {"vi_plus": math.nan, "vi_minus": math.nan}
        self._vm_plus.push(abs(h - self._prev_low))
        self._vm_minus.push(abs(l - self._prev_high))
        self._tr.push(true_range(h, l, self._prev_close))
        self._prev_close, self._prev_high, self._prev_low = c, h, l
        if not self._tr.full:
            return {"vi_plus": math.nan, "vi_minus": math.nan}
        tr_sum = self._tr.sum()
        if tr_sum == 0:
            return {"vi_plus": 0.0, "vi_minus": 0.0}
        return {
            "vi_plus": self._vm_plus.sum() / tr_sum,
            "vi_minus": self._vm_minus.sum() / tr_sum,
        }

    def reset(self) -> None:
        super().reset()
        self._vm_plus.reset()
        self._vm_minus.reset()
        self._tr.reset()
        self._prev_close = self._prev_high = self._prev_low = None


@register("vortex")
def _make_vortex(period: int = 14) -> Indicator:
    return Vortex(period=period)


# ---------------------------------------------------------------- TRIX


class TRIX(Indicator):
    """Percentage change of the triple-smoothed EMA."""

    name = "trix"
    version = "1"
    output_keys = ("value",)

    def __init__(self, period: int = 15, source: Source = "close") -> None:
        super().__init__()
        if period < 1:
            raise ValueError("trix period must be >= 1")
        self._period = period
        self._source = source
        self._ema1 = EMA(period=period, source=source)
        self._ema2 = EMA(period=period, source=source)
        self._ema3 = EMA(period=period, source=source)
        self._prev_triple: float | None = None
        self.warmup = 3 * period

    @property
    def params(self) -> dict[str, Any]:
        return {"period": self._period, "source": self._source}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        r1 = self._ema1.update(bar)
        v1 = r1.values.get("value")
        if v1 is None or not r1.ready:
            return {"value": math.nan}
        v2 = self._ema2.feed_value(v1)
        if v2 is None:
            return {"value": math.nan}
        v3 = self._ema3.feed_value(v2)
        if v3 is None:
            return {"value": math.nan}
        if self._prev_triple is None or self._prev_triple == 0:
            self._prev_triple = v3
            return {"value": math.nan}
        trix = 100.0 * (v3 - self._prev_triple) / self._prev_triple
        self._prev_triple = v3
        return {"value": trix}

    def reset(self) -> None:
        super().reset()
        self._ema1.reset()
        self._ema2.reset()
        self._ema3.reset()
        self._prev_triple = None


@register("trix")
def _make_trix(period: int = 15, source: Source = "close") -> Indicator:
    return TRIX(period=period, source=source)
