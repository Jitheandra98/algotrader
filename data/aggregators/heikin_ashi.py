"""Heikin-Ashi aggregator — derived from upstream OHLCV bars.

Formulas (per instrument):

    HA_close = (O + H + L + C) / 4
    HA_open  = (prev_HA_open + prev_HA_close) / 2   (or (O+C)/2 for the first bar)
    HA_high  = max(H, HA_open, HA_close)
    HA_low   = min(L, HA_open, HA_close)

Volume passes through unchanged. Timeframe and timestamp are preserved.
"""

from __future__ import annotations

from decimal import Decimal

from core.schemas import Bar, Tick


_TWO = Decimal("2")
_FOUR = Decimal("4")


class HeikinAshiAggregator:
    """Maintains per-instrument HA recurrence state."""

    def __init__(self) -> None:
        self._prev: dict[str, Bar] = {}  # last emitted HA bar per instrument

    def on_tick(self, tick: Tick) -> list[Bar]:
        raise NotImplementedError("HeikinAshiAggregator is bar-driven; use on_bar")

    def on_bar(self, bar: Bar) -> list[Bar]:
        key = bar.instrument.key
        ha_close = (bar.open + bar.high + bar.low + bar.close) / _FOUR

        prev = self._prev.get(key)
        if prev is None:
            ha_open = (bar.open + bar.close) / _TWO
        else:
            ha_open = (prev.open + prev.close) / _TWO

        ha_high = max(bar.high, ha_open, ha_close)
        ha_low = min(bar.low, ha_open, ha_close)

        ha_bar = Bar(
            instrument=bar.instrument,
            timeframe=bar.timeframe,
            timestamp=bar.timestamp,
            open=ha_open,
            high=ha_high,
            low=ha_low,
            close=ha_close,
            volume=bar.volume,
            vwap=bar.vwap,
            trade_count=bar.trade_count,
        )
        self._prev[key] = ha_bar
        return [ha_bar]

    def flush(self) -> list[Bar]:
        return []  # HA has no in-flight state separate from source bars
