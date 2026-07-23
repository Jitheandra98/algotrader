"""RSI mean reversion.

Buy when RSI dips below ``oversold`` (default 30) AND price is above a long-term
SMA filter (default 200 bars) — the trend filter prevents catching falling knives
in bear markets. Exit when RSI lifts above ``overbought`` (default 70).

Short-side disabled by default; enable via ``parameters['allow_short']=True``.
Stops are ATR-based to scale with volatility.
"""

from __future__ import annotations

from typing import ClassVar

from core.schemas import Bar, Direction, Signal
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register


class RsiMeanReversionStrategy(Strategy):
    name: ClassVar[str] = "rsi_mean_reversion"
    version: ClassVar[str] = "1"

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        if len(config.instruments) != 1:
            raise ValueError("rsi_mean_reversion is single-instrument")
        p = config.parameters
        self._instrument = config.instruments[0]
        self._rsi_period = int(p.get("rsi_period", 14))
        self._oversold = float(p.get("oversold", 30.0))
        self._overbought = float(p.get("overbought", 70.0))
        self._trend_period = int(p.get("trend_period", 200))
        self._atr_period = int(p.get("atr_period", 14))
        self._stop_atr_mult = float(p.get("stop_atr_mult", 2.0))
        self._allow_short = bool(p.get("allow_short", False))
        self._rsi_key: str | None = None
        self._trend_key: str | None = None
        self._atr_key: str | None = None
        self._in_long = False
        self._in_short = False

    def configure(self, ctx: StrategyContext) -> None:
        self._rsi_key = ctx.request_indicator(self._instrument, "rsi", period=self._rsi_period)
        self._trend_key = ctx.request_indicator(self._instrument, "sma", period=self._trend_period)
        self._atr_key = ctx.request_indicator(self._instrument, "atr", period=self._atr_period)

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if bar.instrument.key != self._instrument.key:
            return
        rsi = ctx.indicator(self._rsi_key) if self._rsi_key else None
        trend = ctx.indicator(self._trend_key) if self._trend_key else None
        atr = ctx.indicator(self._atr_key) if self._atr_key else None
        if not (rsi and trend and atr and rsi.ready and trend.ready and atr.ready):
            return
        rsi_v = rsi.values.get("value")
        trend_v = trend.values.get("value")
        atr_v = atr.values.get("value")
        if rsi_v is None or trend_v is None or atr_v is None:
            return

        price = float(bar.close)
        # Long entry: RSI oversold AND price above trend filter.
        if not self._in_long and not self._in_short and rsi_v < self._oversold and price > trend_v:
            from decimal import Decimal
            stop = Decimal(str(price - self._stop_atr_mult * atr_v))
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.LONG,
                    conviction=min(1.0, (self._oversold - rsi_v) / self._oversold + 0.5),
                    stop_price=stop,
                    rationale=f"RSI={rsi_v:.1f} < {self._oversold}, price>{trend_v:.2f} (uptrend)",
                )
            )
            self._in_long = True
            return

        # Long exit: RSI overbought.
        if self._in_long and rsi_v > self._overbought:
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.EXIT,
                    conviction=1.0,
                    rationale=f"RSI={rsi_v:.1f} > {self._overbought}",
                )
            )
            self._in_long = False
            return

        # Optional short side.
        if self._allow_short:
            if not self._in_short and not self._in_long and rsi_v > self._overbought and price < trend_v:
                from decimal import Decimal
                stop = Decimal(str(price + self._stop_atr_mult * atr_v))
                ctx.emit_signal(
                    Signal(
                        strategy_id=self.name,
                        instrument=self._instrument,
                        direction=Direction.SHORT,
                        conviction=min(1.0, (rsi_v - self._overbought) / (100 - self._overbought) + 0.5),
                        stop_price=stop,
                        rationale=f"RSI={rsi_v:.1f} > {self._overbought}, price<{trend_v:.2f}",
                    )
                )
                self._in_short = True
                return
            if self._in_short and rsi_v < self._oversold:
                ctx.emit_signal(
                    Signal(
                        strategy_id=self.name,
                        instrument=self._instrument,
                        direction=Direction.EXIT,
                        conviction=1.0,
                        rationale=f"RSI={rsi_v:.1f} < {self._oversold}",
                    )
                )
                self._in_short = False


@register("rsi_mean_reversion")
def _make(config: StrategyConfig) -> Strategy:
    return RsiMeanReversionStrategy(config)
