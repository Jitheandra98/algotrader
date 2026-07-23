"""Volatility Contraction Pattern (VCP) breakout (Minervini-style).

Uses the ``pattern.vcp`` indicator: enter LONG when:
  - VCP detected (3+ contractions, volume dryup), AND
  - current bar closes above the recent N-bar high (the pivot), AND
  - volume confirms (current volume > rolling mean × ``volume_breakout_mult``).

Stop is ATR-based or last swing low, whichever is closer. Exit on stop or
fixed-R take-profit.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from typing import ClassVar

from core.schemas import Bar, Direction, Signal
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register


class VcpBreakoutStrategy(Strategy):
    name: ClassVar[str] = "vcp_breakout"
    version: ClassVar[str] = "1"

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        if len(config.instruments) != 1:
            raise ValueError("vcp_breakout is single-instrument")
        p = config.parameters
        self._instrument = config.instruments[0]
        self._pivot_lookback = int(p.get("pivot_lookback", 20))
        self._volume_lookback = int(p.get("volume_lookback", 20))
        self._volume_breakout_mult = float(p.get("volume_breakout_mult", 1.5))
        self._atr_period = int(p.get("atr_period", 14))
        self._stop_atr_mult = float(p.get("stop_atr_mult", 2.0))
        self._target_r_mult = float(p.get("target_r_mult", 3.0))
        self._vcp_key: str | None = None
        self._atr_key: str | None = None
        self._highs: deque[float] = deque(maxlen=self._pivot_lookback)
        self._volumes: deque[float] = deque(maxlen=self._volume_lookback)
        self._in_position = False

    def configure(self, ctx: StrategyContext) -> None:
        self._vcp_key = ctx.request_indicator(self._instrument, "pattern.vcp")
        self._atr_key = ctx.request_indicator(self._instrument, "atr", period=self._atr_period)

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if bar.instrument.key != self._instrument.key:
            return

        # Maintain pivot lookback and volume average.
        self._highs.append(float(bar.high))
        self._volumes.append(float(bar.volume))
        if len(self._highs) < self._pivot_lookback or len(self._volumes) < self._volume_lookback:
            return

        vcp = ctx.indicator(self._vcp_key) if self._vcp_key else None
        atr = ctx.indicator(self._atr_key) if self._atr_key else None
        if not (vcp and vcp.ready and atr and atr.ready):
            return
        vcp_signal = vcp.values.get("signal", 0.0)
        atr_v = atr.values.get("value")
        if atr_v is None:
            return

        # Pivot = highest high over lookback (excluding current bar to avoid the
        # breakout itself dominating the level).
        prior_pivot = max(list(self._highs)[:-1])
        price = float(bar.close)
        vol_mean = sum(list(self._volumes)[:-1]) / (len(self._volumes) - 1)
        volume_confirm = vol_mean > 0 and float(bar.volume) / vol_mean >= self._volume_breakout_mult

        if not self._in_position and vcp_signal >= 1.0 and price > prior_pivot and volume_confirm:
            stop = Decimal(str(price - self._stop_atr_mult * atr_v))
            target = Decimal(str(price + self._stop_atr_mult * atr_v * self._target_r_mult))
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.LONG,
                    conviction=0.8,
                    target_price=target,
                    stop_price=stop,
                    rationale=(
                        f"VCP breakout: close={price:.2f} > pivot={prior_pivot:.2f}, "
                        f"vol={float(bar.volume)/vol_mean:.2f}x avg"
                    ),
                )
            )
            self._in_position = True
            return

        # Exit on close back under the pivot (broken breakout).
        if self._in_position and price < prior_pivot:
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.EXIT,
                    conviction=1.0,
                    rationale=f"close={price:.2f} fell back under pivot={prior_pivot:.2f}",
                )
            )
            self._in_position = False


@register("vcp_breakout")
def _make(config: StrategyConfig) -> Strategy:
    return VcpBreakoutStrategy(config)
