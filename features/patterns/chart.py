"""Chart pattern detectors.

These are heuristic — chart patterns are inherently fuzzy. The detectors operate
on confirmed swing points (see :class:`SwingDetector`) and emit a 0.0/1.0 signal
when the pattern's structural conditions are met.

Implemented:
  - Head & Shoulders (top + inverse/bottom)
  - Double Top / Double Bottom
  - VCP (Volatility Contraction Pattern, Minervini style)
  - Ascending / descending triangles (from regression slopes on swing series)

Strategies that consume these should treat them as *suggestions* — pair with
volume/trend filters before acting.
"""

from __future__ import annotations

from typing import Any

from core.schemas import Bar
from features.base import Indicator
from features.patterns._swings import Swing, SwingDetector
from features.registry import register


# ---------------------------------------------------------------- H&S


class HeadAndShoulders(Indicator):
    """Classic 5-swing H&S top: LH, HH, head, HH', LH' where head is the highest
    and the two shoulders + two intervening lows form a roughly symmetric pattern.

    Tolerance defaults match what most platforms use:
      - shoulder height within ``shoulder_tol_pct`` of each other (default 5%)
      - neckline lows within ``neckline_tol_pct`` (default 3%)
      - head must exceed shoulders by ``head_min_pct`` (default 2%)
    """

    name = "pattern.head_and_shoulders"
    version = "1"
    output_keys = ("signal", "neckline")

    def __init__(
        self,
        window: int = 3,
        shoulder_tol_pct: float = 0.05,
        neckline_tol_pct: float = 0.03,
        head_min_pct: float = 0.02,
    ) -> None:
        super().__init__()
        self._sw = SwingDetector(window=window)
        self._shoulder_tol = shoulder_tol_pct
        self._neckline_tol = neckline_tol_pct
        self._head_min = head_min_pct
        self.warmup = 2 * window + 1

    @property
    def params(self) -> dict[str, Any]:
        return {
            "window": self._sw.window,
            "shoulder_tol_pct": self._shoulder_tol,
            "neckline_tol_pct": self._neckline_tol,
            "head_min_pct": self._head_min,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        import math

        self._sw.push(bar)
        swings = self._sw.confirmed
        # Need at least 5 swings: high-low-high-low-high
        highs = [s for s in swings if s.kind == "high"][-3:]
        lows = [s for s in swings if s.kind == "low"][-2:]
        if len(highs) < 3 or len(lows) < 2:
            return {"signal": 0.0, "neckline": math.nan}
        ls, head, rs = highs[-3], highs[-2], highs[-1]
        n1, n2 = lows[-2], lows[-1]

        # Order check: ls < n1 < head < n2 < rs by time
        ts = [ls.timestamp, n1.timestamp, head.timestamp, n2.timestamp, rs.timestamp]
        if ts != sorted(ts):
            return {"signal": 0.0, "neckline": math.nan}

        # Head dominance
        if head.price <= max(ls.price, rs.price) * (1 + self._head_min):
            return {"signal": 0.0, "neckline": math.nan}
        # Shoulder symmetry
        if abs(ls.price - rs.price) / max(ls.price, rs.price) > self._shoulder_tol:
            return {"signal": 0.0, "neckline": math.nan}
        # Neckline symmetry
        if abs(n1.price - n2.price) / max(n1.price, n2.price) > self._neckline_tol:
            return {"signal": 0.0, "neckline": math.nan}

        neckline = (n1.price + n2.price) / 2.0
        # Confirmation: price closes below neckline.
        if float(bar.close) < neckline:
            return {"signal": 1.0, "neckline": neckline}
        return {"signal": 0.0, "neckline": neckline}

    def reset(self) -> None:
        super().reset()
        self._sw.reset()


@register("pattern.head_and_shoulders")
def _make_hs(
    window: int = 3,
    shoulder_tol_pct: float = 0.05,
    neckline_tol_pct: float = 0.03,
    head_min_pct: float = 0.02,
) -> Indicator:
    return HeadAndShoulders(
        window=window,
        shoulder_tol_pct=shoulder_tol_pct,
        neckline_tol_pct=neckline_tol_pct,
        head_min_pct=head_min_pct,
    )


class InverseHeadAndShoulders(Indicator):
    """Inverse H&S (bottom)."""

    name = "pattern.inverse_head_and_shoulders"
    version = "1"
    output_keys = ("signal", "neckline")

    def __init__(
        self,
        window: int = 3,
        shoulder_tol_pct: float = 0.05,
        neckline_tol_pct: float = 0.03,
        head_min_pct: float = 0.02,
    ) -> None:
        super().__init__()
        self._sw = SwingDetector(window=window)
        self._shoulder_tol = shoulder_tol_pct
        self._neckline_tol = neckline_tol_pct
        self._head_min = head_min_pct
        self.warmup = 2 * window + 1

    @property
    def params(self) -> dict[str, Any]:
        return {
            "window": self._sw.window,
            "shoulder_tol_pct": self._shoulder_tol,
            "neckline_tol_pct": self._neckline_tol,
            "head_min_pct": self._head_min,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        import math

        self._sw.push(bar)
        swings = self._sw.confirmed
        lows = [s for s in swings if s.kind == "low"][-3:]
        highs = [s for s in swings if s.kind == "high"][-2:]
        if len(lows) < 3 or len(highs) < 2:
            return {"signal": 0.0, "neckline": math.nan}
        ls, head, rs = lows[-3], lows[-2], lows[-1]
        n1, n2 = highs[-2], highs[-1]
        ts = [ls.timestamp, n1.timestamp, head.timestamp, n2.timestamp, rs.timestamp]
        if ts != sorted(ts):
            return {"signal": 0.0, "neckline": math.nan}
        if head.price >= min(ls.price, rs.price) * (1 - self._head_min):
            return {"signal": 0.0, "neckline": math.nan}
        if abs(ls.price - rs.price) / max(ls.price, rs.price) > self._shoulder_tol:
            return {"signal": 0.0, "neckline": math.nan}
        if abs(n1.price - n2.price) / max(n1.price, n2.price) > self._neckline_tol:
            return {"signal": 0.0, "neckline": math.nan}
        neckline = (n1.price + n2.price) / 2.0
        if float(bar.close) > neckline:
            return {"signal": 1.0, "neckline": neckline}
        return {"signal": 0.0, "neckline": neckline}

    def reset(self) -> None:
        super().reset()
        self._sw.reset()


@register("pattern.inverse_head_and_shoulders")
def _make_ihs(
    window: int = 3,
    shoulder_tol_pct: float = 0.05,
    neckline_tol_pct: float = 0.03,
    head_min_pct: float = 0.02,
) -> Indicator:
    return InverseHeadAndShoulders(
        window=window,
        shoulder_tol_pct=shoulder_tol_pct,
        neckline_tol_pct=neckline_tol_pct,
        head_min_pct=head_min_pct,
    )


# ---------------------------------------------------------------- Double Top / Bottom


class DoubleTop(Indicator):
    """Two swing highs at similar prices separated by a swing low.

    Confirms on a close below the swing low.
    """

    name = "pattern.double_top"
    version = "1"
    output_keys = ("signal", "level")

    def __init__(self, window: int = 3, tol_pct: float = 0.03) -> None:
        super().__init__()
        self._sw = SwingDetector(window=window)
        self._tol = tol_pct
        self.warmup = 2 * window + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"window": self._sw.window, "tol_pct": self._tol}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        import math

        self._sw.push(bar)
        swings = self._sw.confirmed
        highs = [s for s in swings if s.kind == "high"][-2:]
        lows = [s for s in swings if s.kind == "low"][-1:]
        if len(highs) < 2 or len(lows) < 1:
            return {"signal": 0.0, "level": math.nan}
        h1, h2 = highs[-2], highs[-1]
        mid_low = lows[-1]
        if not (h1.timestamp < mid_low.timestamp < h2.timestamp):
            return {"signal": 0.0, "level": math.nan}
        if abs(h1.price - h2.price) / max(h1.price, h2.price) > self._tol:
            return {"signal": 0.0, "level": math.nan}
        if float(bar.close) < mid_low.price:
            return {"signal": 1.0, "level": mid_low.price}
        return {"signal": 0.0, "level": mid_low.price}

    def reset(self) -> None:
        super().reset()
        self._sw.reset()


@register("pattern.double_top")
def _make_double_top(window: int = 3, tol_pct: float = 0.03) -> Indicator:
    return DoubleTop(window=window, tol_pct=tol_pct)


class DoubleBottom(Indicator):
    name = "pattern.double_bottom"
    version = "1"
    output_keys = ("signal", "level")

    def __init__(self, window: int = 3, tol_pct: float = 0.03) -> None:
        super().__init__()
        self._sw = SwingDetector(window=window)
        self._tol = tol_pct
        self.warmup = 2 * window + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"window": self._sw.window, "tol_pct": self._tol}

    def _update(self, bar: Bar) -> dict[str, float] | None:
        import math

        self._sw.push(bar)
        swings = self._sw.confirmed
        lows = [s for s in swings if s.kind == "low"][-2:]
        highs = [s for s in swings if s.kind == "high"][-1:]
        if len(lows) < 2 or len(highs) < 1:
            return {"signal": 0.0, "level": math.nan}
        l1, l2 = lows[-2], lows[-1]
        mid_high = highs[-1]
        if not (l1.timestamp < mid_high.timestamp < l2.timestamp):
            return {"signal": 0.0, "level": math.nan}
        if abs(l1.price - l2.price) / max(l1.price, l2.price) > self._tol:
            return {"signal": 0.0, "level": math.nan}
        if float(bar.close) > mid_high.price:
            return {"signal": 1.0, "level": mid_high.price}
        return {"signal": 0.0, "level": mid_high.price}

    def reset(self) -> None:
        super().reset()
        self._sw.reset()


@register("pattern.double_bottom")
def _make_double_bottom(window: int = 3, tol_pct: float = 0.03) -> Indicator:
    return DoubleBottom(window=window, tol_pct=tol_pct)


# ---------------------------------------------------------------- VCP


class VolatilityContractionPattern(Indicator):
    """Minervini-style VCP.

    Watches for ``contractions_required`` (default 3) successive pullbacks where
    each pullback's depth (from prior swing high) is smaller than the previous.
    Volume should also dry up — checked via rolling RVOL < ``volume_dryup_max``.
    """

    name = "pattern.vcp"
    version = "1"
    output_keys = ("signal", "contractions", "last_pullback_pct")

    def __init__(
        self,
        window: int = 3,
        contractions_required: int = 3,
        max_pullback_pct: float = 0.25,
        volume_dryup_max: float = 1.0,
        volume_lookback: int = 20,
    ) -> None:
        super().__init__()
        if contractions_required < 2:
            raise ValueError("VCP needs >= 2 contractions")
        self._sw = SwingDetector(window=window)
        self._n = contractions_required
        self._max_pull = max_pullback_pct
        self._vd = volume_dryup_max
        self._vl = volume_lookback
        self._volumes: list[float] = []
        self.warmup = 2 * window + 1

    @property
    def params(self) -> dict[str, Any]:
        return {
            "window": self._sw.window,
            "contractions_required": self._n,
            "max_pullback_pct": self._max_pull,
            "volume_dryup_max": self._vd,
            "volume_lookback": self._vl,
        }

    def _update(self, bar: Bar) -> dict[str, float] | None:
        import math

        self._sw.push(bar)
        self._volumes.append(float(bar.volume))
        if len(self._volumes) > self._vl:
            self._volumes.pop(0)

        swings = self._sw.confirmed
        # Find alternating high-low sequence at the end.
        # We need: H1 L1 H2 L2 ... Hk Lk where each (Hi-Li)/Hi < (Hi-1 - Li-1)/Hi-1
        # And current high should be near the high of last swing high.
        pairs: list[tuple[Swing, Swing]] = []
        i = len(swings) - 1
        while i >= 1 and len(pairs) < self._n:
            top = swings[i - 1]
            bot = swings[i]
            if top.kind == "high" and bot.kind == "low" and top.timestamp < bot.timestamp:
                pairs.append((top, bot))
                i -= 2
            else:
                break
        pairs.reverse()
        if len(pairs) < self._n:
            return {
                "signal": 0.0,
                "contractions": float(len(pairs)),
                "last_pullback_pct": math.nan,
            }
        pullbacks = [(t.price - b.price) / t.price for t, b in pairs]
        last_pb = pullbacks[-1]
        if any(p > self._max_pull for p in pullbacks):
            return {
                "signal": 0.0,
                "contractions": float(len(pairs)),
                "last_pullback_pct": last_pb,
            }
        # Each pullback strictly smaller than previous.
        decreasing = all(pullbacks[i] < pullbacks[i - 1] for i in range(1, len(pullbacks)))

        # Volume dry-up: current bar volume < volume_dryup_max × rolling mean.
        volume_ok = True
        if len(self._volumes) >= self._vl:
            mean_v = sum(self._volumes[:-1]) / (len(self._volumes) - 1)
            if mean_v > 0 and float(bar.volume) / mean_v > self._vd:
                volume_ok = False

        signal = 1.0 if decreasing and volume_ok else 0.0
        return {
            "signal": signal,
            "contractions": float(len(pairs)),
            "last_pullback_pct": last_pb,
        }

    def reset(self) -> None:
        super().reset()
        self._sw.reset()
        self._volumes.clear()


@register("pattern.vcp")
def _make_vcp(
    window: int = 3,
    contractions_required: int = 3,
    max_pullback_pct: float = 0.25,
    volume_dryup_max: float = 1.0,
    volume_lookback: int = 20,
) -> Indicator:
    return VolatilityContractionPattern(
        window=window,
        contractions_required=contractions_required,
        max_pullback_pct=max_pullback_pct,
        volume_dryup_max=volume_dryup_max,
        volume_lookback=volume_lookback,
    )
