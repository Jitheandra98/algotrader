"""SMA crossover — the canonical reference strategy.

Long when the fast SMA crosses above the slow SMA; flat when it crosses back below.
Single instrument, single-direction (no shorts), no stops. Designed as a sanity
benchmark for the engine, not a production strategy.

This file lives in ``strategies/`` because that's its proper home — M5 fleshes out
the registry and the other 7 reference strategies around it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from core.schemas import Bar, Direction, Signal
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register


class SmaCrossoverStrategy(Strategy):
    name: ClassVar[str] = "sma_crossover"
    version: ClassVar[str] = "1"

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._fast_period = int(config.parameters.get("fast_period", 20))
        self._slow_period = int(config.parameters.get("slow_period", 50))
        if self._fast_period >= self._slow_period:
            raise ValueError("fast_period must be < slow_period")
        if len(config.instruments) != 1:
            raise ValueError("sma_crossover is single-instrument")
        self._instrument = config.instruments[0]
        self._fast_key: str | None = None
        self._slow_key: str | None = None
        self._prev_diff: float | None = None  # for crossover detection
        self._in_position = False

    def configure(self, ctx: StrategyContext) -> None:
        self._fast_key = ctx.request_indicator(
            self._instrument, "sma", period=self._fast_period
        )
        self._slow_key = ctx.request_indicator(
            self._instrument, "sma", period=self._slow_period
        )

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if bar.instrument.key != self._instrument.key:
            return
        assert self._fast_key is not None and self._slow_key is not None
        fast = ctx.indicator(self._fast_key)
        slow = ctx.indicator(self._slow_key)
        if fast is None or slow is None or not (fast.ready and slow.ready):
            return
        f_val = fast.values.get("value")
        s_val = slow.values.get("value")
        if f_val is None or s_val is None:
            return

        diff = f_val - s_val
        prev = self._prev_diff
        self._prev_diff = diff
        if prev is None:
            return

        # Bullish crossover: prev <= 0 and now > 0
        if prev <= 0 and diff > 0 and not self._in_position:
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.LONG,
                    conviction=1.0,
                    rationale=(
                        f"fast SMA({self._fast_period})={f_val:.2f} crossed above "
                        f"slow SMA({self._slow_period})={s_val:.2f}"
                    ),
                )
            )
            self._in_position = True
        # Bearish crossover: prev >= 0 and now < 0
        elif prev >= 0 and diff < 0 and self._in_position:
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.EXIT,
                    conviction=1.0,
                    rationale=(
                        f"fast SMA({self._fast_period})={f_val:.2f} crossed below "
                        f"slow SMA({self._slow_period})={s_val:.2f}"
                    ),
                )
            )
            self._in_position = False


@register("sma_crossover")
def _make_sma_crossover(config: StrategyConfig) -> Strategy:
    return SmaCrossoverStrategy(config)
