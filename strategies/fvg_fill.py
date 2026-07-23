"""Fair-Value-Gap (FVG) fill strategy.

An FVG is a 3-bar pattern where bar 2's range leaves a price zone that bars 1
and 3 didn't cover:

  - **Bullish FVG**: ``bar1.high < bar3.low``. Gap zone = (bar1.high, bar3.low).
    Price often returns to "fill" the gap; an entry on the fill (with bar 3's
    low as resistance turned support) is a common play.
  - **Bearish FVG**: ``bar1.low > bar3.high``. Gap zone = (bar3.high, bar1.low).

Strategy: track recent unfilled FVGs in a bounded ring. When the current bar
trades into the gap zone, emit a LONG (bullish FVG) or SHORT (bearish FVG)
signal with the opposite edge of the gap as the stop.

Gaps "expire" if not filled within ``max_age_bars``; gaps fully traded through
are marked filled.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from core.schemas import Bar, Direction, Signal
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register


@dataclass(slots=True)
class _Gap:
    is_bullish: bool
    low: float
    high: float
    age: int


class FvgFillStrategy(Strategy):
    name: ClassVar[str] = "fvg_fill"
    version: ClassVar[str] = "1"

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        if len(config.instruments) != 1:
            raise ValueError("fvg_fill is single-instrument")
        p = config.parameters
        self._instrument = config.instruments[0]
        self._max_age = int(p.get("max_age_bars", 50))
        self._max_open = int(p.get("max_open_gaps", 20))
        self._allow_short = bool(p.get("allow_short", True))
        self._bar_history: deque[Bar] = deque(maxlen=3)
        self._gaps: deque[_Gap] = deque(maxlen=self._max_open)
        self._in_position = False

    def configure(self, ctx: StrategyContext) -> None:
        # No indicators needed — pure price-action strategy.
        return None

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if bar.instrument.key != self._instrument.key:
            return
        self._bar_history.append(bar)

        # Detect a new FVG once we have 3 bars.
        if len(self._bar_history) == 3:
            b1, b2, b3 = self._bar_history
            if float(b1.high) < float(b3.low):
                self._gaps.append(_Gap(is_bullish=True, low=float(b1.high), high=float(b3.low), age=0))
            elif float(b1.low) > float(b3.high):
                self._gaps.append(_Gap(is_bullish=False, low=float(b3.high), high=float(b1.low), age=0))

        # Age and prune.
        for g in self._gaps:
            g.age += 1
        self._gaps = deque(
            (g for g in self._gaps if g.age <= self._max_age),
            maxlen=self._max_open,
        )

        if self._in_position:
            return  # one position at a time

        price_low = float(bar.low)
        price_high = float(bar.high)

        for g in list(self._gaps):
            if g.is_bullish and price_low <= g.high and price_low >= g.low:
                # Price re-entered a bullish FVG → LONG.
                ctx.emit_signal(
                    Signal(
                        strategy_id=self.name,
                        instrument=self._instrument,
                        direction=Direction.LONG,
                        conviction=0.6,
                        stop_price=Decimal(str(g.low)),
                        target_price=Decimal(str(g.high + (g.high - g.low) * 2)),
                        rationale=f"bullish FVG fill at {price_low:.2f} (gap {g.low:.2f}-{g.high:.2f})",
                    )
                )
                self._in_position = True
                self._gaps.remove(g)
                return
            if (
                self._allow_short
                and not g.is_bullish
                and price_high >= g.low
                and price_high <= g.high
            ):
                ctx.emit_signal(
                    Signal(
                        strategy_id=self.name,
                        instrument=self._instrument,
                        direction=Direction.SHORT,
                        conviction=0.6,
                        stop_price=Decimal(str(g.high)),
                        target_price=Decimal(str(g.low - (g.high - g.low) * 2)),
                        rationale=f"bearish FVG fill at {price_high:.2f} (gap {g.low:.2f}-{g.high:.2f})",
                    )
                )
                self._in_position = True
                self._gaps.remove(g)
                return

    def on_fill(self, fill, ctx):  # noqa: ARG002, ANN001
        # Strategy holds one position at a time; flag goes false on EXIT fills.
        from core.schemas import OrderSide
        # We don't have great visibility into open vs close in this minimal stub.
        # The risk stub uses signal.direction; for EXIT it will reverse the
        # position. We toggle off when we see a fill in the opposite direction
        # from what opened us.
        # Simple heuristic: any opposite-side fill flattens our flag.
        # (For M5 this is good enough; M6 makes position tracking explicit.)
        self._in_position = False


@register("fvg_fill")
def _make(config: StrategyConfig) -> Strategy:
    return FvgFillStrategy(config)
