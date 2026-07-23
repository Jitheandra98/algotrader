"""Bollinger Band squeeze breakout (TTM-style).

A *squeeze* exists when Bollinger Bands are inside Keltner Channels — volatility
is unusually compressed. When the bands re-expand (squeeze "fires"), price often
moves directionally. Strategy:

  - Detect squeeze: BB upper < Keltner upper AND BB lower > Keltner lower.
  - Wait for the bands to release (no longer inside Keltner).
  - On release: take direction from price-vs-bbands-middle (above → LONG, below → SHORT).
  - Stop: opposite band; exit on opposite-band touch.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from core.schemas import Bar, Direction, Signal
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register


class BollingerSqueezeStrategy(Strategy):
    name: ClassVar[str] = "bollinger_squeeze"
    version: ClassVar[str] = "1"

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        if len(config.instruments) != 1:
            raise ValueError("bollinger_squeeze is single-instrument")
        p = config.parameters
        self._instrument = config.instruments[0]
        self._bb_period = int(p.get("bb_period", 20))
        self._bb_k = float(p.get("bb_k", 2.0))
        self._kelt_period = int(p.get("keltner_period", 20))
        self._kelt_k = float(p.get("keltner_k", 1.5))
        self._allow_short = bool(p.get("allow_short", True))
        self._bb_key: str | None = None
        self._kelt_key: str | None = None
        self._was_squeezed = False  # one-bar memory for release detection
        self._in_long = False
        self._in_short = False

    def configure(self, ctx: StrategyContext) -> None:
        self._bb_key = ctx.request_indicator(
            self._instrument, "bbands", period=self._bb_period, k=self._bb_k
        )
        self._kelt_key = ctx.request_indicator(
            self._instrument, "keltner", period=self._kelt_period, atr_period=self._kelt_period, k=self._kelt_k
        )

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if bar.instrument.key != self._instrument.key:
            return
        bb = ctx.indicator(self._bb_key) if self._bb_key else None
        kelt = ctx.indicator(self._kelt_key) if self._kelt_key else None
        if not (bb and bb.ready and kelt and kelt.ready):
            return
        bb_upper = bb.values["upper"]
        bb_lower = bb.values["lower"]
        bb_middle = bb.values["middle"]
        k_upper = kelt.values["upper"]
        k_lower = kelt.values["lower"]
        price = float(bar.close)

        squeezed = bb_upper < k_upper and bb_lower > k_lower

        # Detect release: previously squeezed, now not.
        if self._was_squeezed and not squeezed and not (self._in_long or self._in_short):
            if price > bb_middle:
                ctx.emit_signal(
                    Signal(
                        strategy_id=self.name,
                        instrument=self._instrument,
                        direction=Direction.LONG,
                        conviction=0.7,
                        stop_price=Decimal(str(bb_lower)),
                        rationale=(
                            f"BB squeeze released: price={price:.2f} > middle={bb_middle:.2f}"
                        ),
                    )
                )
                self._in_long = True
            elif self._allow_short and price < bb_middle:
                ctx.emit_signal(
                    Signal(
                        strategy_id=self.name,
                        instrument=self._instrument,
                        direction=Direction.SHORT,
                        conviction=0.7,
                        stop_price=Decimal(str(bb_upper)),
                        rationale=(
                            f"BB squeeze released: price={price:.2f} < middle={bb_middle:.2f}"
                        ),
                    )
                )
                self._in_short = True

        # Exits on opposite band touch.
        if self._in_long and price >= bb_upper:
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.EXIT,
                    conviction=1.0,
                    rationale=f"long exit: price={price:.2f} hit upper band {bb_upper:.2f}",
                )
            )
            self._in_long = False
        elif self._in_short and price <= bb_lower:
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.EXIT,
                    conviction=1.0,
                    rationale=f"short exit: price={price:.2f} hit lower band {bb_lower:.2f}",
                )
            )
            self._in_short = False

        self._was_squeezed = squeezed


@register("bollinger_squeeze")
def _make(config: StrategyConfig) -> Strategy:
    return BollingerSqueezeStrategy(config)
