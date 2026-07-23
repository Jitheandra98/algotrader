"""Range bar aggregator — bar closes when (high - low) >= range_size.

Per instrument: maintain a buffer; on every tick, update high/low/close and check
whether the range has expanded past the threshold. If yes, emit and start a fresh
bar with the closing tick's price as the new open.

If a single tick has a huge gap that crosses several range thresholds, we may emit
multiple bars from one tick. Each subsequent bar has the previous close as its open.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from core.schemas import Bar, BarTimeframe, Instrument, Tick


class _RangeBuffer:
    __slots__ = (
        "instrument", "open", "high", "low", "close", "volume",
        "trade_count", "vwap_num", "vwap_den", "open_ts", "last_ts",
    )

    def __init__(self, instrument: Instrument, open_: Decimal, ts: datetime) -> None:
        self.instrument = instrument
        self.open = open_
        self.high = open_
        self.low = open_
        self.close = open_
        self.volume: Decimal = Decimal("0")
        self.trade_count = 0
        self.vwap_num: Decimal = Decimal("0")
        self.vwap_den: Decimal = Decimal("0")
        self.open_ts = ts
        self.last_ts = ts

    def update(self, price: Decimal, size: Decimal | None, ts: datetime) -> None:
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        self.last_ts = ts
        if size is not None and size > 0:
            self.volume += size
            self.trade_count += 1
            self.vwap_num += price * size
            self.vwap_den += size

    def to_bar(self, timeframe: BarTimeframe) -> Bar:
        vwap = (self.vwap_num / self.vwap_den) if self.vwap_den > 0 else None
        return Bar(
            instrument=self.instrument,
            timeframe=timeframe,
            timestamp=self.open_ts,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            vwap=vwap,
            trade_count=self.trade_count,
        )


class RangeBarAggregator:
    """Range bars at a single range_size. Per-instrument state."""

    def __init__(self, range_size: Decimal, timeframe: BarTimeframe = BarTimeframe.M1) -> None:
        if range_size <= 0:
            raise ValueError("range_size must be positive")
        self._range = range_size
        self._timeframe = timeframe
        self._buffers: dict[str, _RangeBuffer] = {}

    def on_tick(self, tick: Tick) -> list[Bar]:
        price = tick.last_price
        if price is None and tick.bid is not None and tick.ask is not None:
            price = (tick.bid + tick.ask) / Decimal("2")
        elif price is None:
            price = tick.bid or tick.ask
        if price is None:
            return []

        key = tick.instrument.key
        buf = self._buffers.get(key)
        if buf is None:
            self._buffers[key] = _RangeBuffer(tick.instrument, price, tick.timestamp)
            buf = self._buffers[key]
            if tick.last_size is not None and tick.last_size > 0:
                buf.update(price, tick.last_size, tick.timestamp)
            return []

        buf.update(price, tick.last_size, tick.timestamp)

        closed: list[Bar] = []
        while buf.high - buf.low >= self._range:
            closed.append(buf.to_bar(self._timeframe))
            new_open = buf.close
            new_buf = _RangeBuffer(tick.instrument, new_open, tick.timestamp)
            self._buffers[key] = new_buf
            buf = new_buf
        return closed

    def on_bar(self, bar: Bar) -> list[Bar]:
        raise NotImplementedError("RangeBarAggregator is tick-driven; use on_tick")

    def flush(self) -> list[Bar]:
        out = [b.to_bar(self._timeframe) for b in self._buffers.values()]
        self._buffers.clear()
        return out
