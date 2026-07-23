"""TD Sequential reversal — DeMark's bar-count-based reversal play.

Enter LONG when buy setup hits 9 (nine consecutive closes lower than the close 4
bars earlier — exhausted downside). Enter SHORT (optional) when sell setup hits
9. Optional trend filter: only take longs above a long SMA, shorts below it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from core.schemas import Bar, Direction, Signal
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register


class TdSequentialStrategy(Strategy):
    name: ClassVar[str] = "td_sequential_reversal"
    version: ClassVar[str] = "1"

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        if len(config.instruments) != 1:
            raise ValueError("td_sequential_reversal is single-instrument")
        p = config.parameters
        self._instrument = config.instruments[0]
        self._setup_threshold = int(p.get("setup_threshold", 9))
        self._allow_short = bool(p.get("allow_short", True))
        self._use_trend_filter = bool(p.get("use_trend_filter", False))
        self._trend_period = int(p.get("trend_period", 200))
        self._atr_period = int(p.get("atr_period", 14))
        self._stop_atr_mult = float(p.get("stop_atr_mult", 2.0))
        self._tdseq_key: str | None = None
        self._trend_key: str | None = None
        self._atr_key: str | None = None
        self._in_long = False
        self._in_short = False
        self._fired_buy = False
        self._fired_sell = False

    def configure(self, ctx: StrategyContext) -> None:
        self._tdseq_key = ctx.request_indicator(self._instrument, "tdseq")
        self._atr_key = ctx.request_indicator(self._instrument, "atr", period=self._atr_period)
        if self._use_trend_filter:
            self._trend_key = ctx.request_indicator(
                self._instrument, "sma", period=self._trend_period
            )

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if bar.instrument.key != self._instrument.key:
            return
        tdseq = ctx.indicator(self._tdseq_key) if self._tdseq_key else None
        atr = ctx.indicator(self._atr_key) if self._atr_key else None
        if not (tdseq and tdseq.ready and atr and atr.ready):
            return
        atr_v = atr.values.get("value")
        if atr_v is None:
            return

        buy_count = tdseq.values.get("setup_buy", 0)
        sell_count = tdseq.values.get("setup_sell", 0)
        price = float(bar.close)

        trend_ok_long = True
        trend_ok_short = True
        if self._use_trend_filter and self._trend_key:
            trend = ctx.indicator(self._trend_key)
            if trend and trend.ready:
                t = trend.values["value"]
                trend_ok_long = price > t
                trend_ok_short = price < t

        # LONG on buy setup 9 (one-shot per setup).
        if buy_count >= self._setup_threshold and not self._fired_buy and not self._in_long and trend_ok_long:
            stop = Decimal(str(price - self._stop_atr_mult * atr_v))
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.LONG,
                    conviction=0.7,
                    stop_price=stop,
                    rationale=f"TD buy setup {int(buy_count)} reached",
                )
            )
            self._in_long = True
            self._fired_buy = True

        # SHORT on sell setup 9.
        if (
            self._allow_short
            and sell_count >= self._setup_threshold
            and not self._fired_sell
            and not self._in_short
            and trend_ok_short
        ):
            stop = Decimal(str(price + self._stop_atr_mult * atr_v))
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.SHORT,
                    conviction=0.7,
                    stop_price=stop,
                    rationale=f"TD sell setup {int(sell_count)} reached",
                )
            )
            self._in_short = True
            self._fired_sell = True

        # Reset "fired" latches once the counts drop back to 0.
        if buy_count == 0:
            self._fired_buy = False
        if sell_count == 0:
            self._fired_sell = False

        # Exits: TD reversal trades typically exit on opposite setup or on a
        # standard ATR trailing stop. We use a simple opposite-setup exit here.
        if self._in_long and sell_count >= self._setup_threshold:
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.EXIT,
                    conviction=1.0,
                    rationale=f"opposite TD sell setup {int(sell_count)} — exit long",
                )
            )
            self._in_long = False
        if self._in_short and buy_count >= self._setup_threshold:
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=self._instrument,
                    direction=Direction.EXIT,
                    conviction=1.0,
                    rationale=f"opposite TD buy setup {int(buy_count)} — exit short",
                )
            )
            self._in_short = False


@register("td_sequential_reversal")
def _make(config: StrategyConfig) -> Strategy:
    return TdSequentialStrategy(config)
