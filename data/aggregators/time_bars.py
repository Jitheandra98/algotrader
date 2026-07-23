"""Wall-clock time bar aggregator.

Buckets ticks into fixed-duration windows aligned to the epoch. A new tick whose
floor-bucketed timestamp differs from the current bucket closes the in-progress bar
and emits it.

Volume / VWAP semantics:
  - Volume is the sum of ``last_size`` across ticks that carry a last trade.
  - Trade count is the count of those ticks.
  - VWAP is Σ(last_price·last_size) / Σ(last_size). If no traded ticks land in the
    bucket, VWAP is None.

Quote-only ticks (no ``last_price``) still mark the bar's open if first, and update
high/low via the mid-price (``(bid+ask)/2``). This matches Alpaca's behaviour when
trades are sparse.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.schemas import Bar, BarTimeframe, Instrument, Tick


_INTERVAL_SECONDS: dict[BarTimeframe, int] = {
    BarTimeframe.S1: 1,
    BarTimeframe.S5: 5,
    BarTimeframe.S15: 15,
    BarTimeframe.M1: 60,
    BarTimeframe.M5: 300,
    BarTimeframe.M15: 900,
    BarTimeframe.M30: 1800,
    BarTimeframe.H1: 3600,
    BarTimeframe.H4: 14_400,
    BarTimeframe.D1: 86_400,
    BarTimeframe.W1: 604_800,
}


def _bucket_start(ts: datetime, interval_s: int) -> datetime:
    """Floor ``ts`` to the nearest multiple of ``interval_s`` seconds (UTC)."""
    epoch = int(ts.timestamp())
    bucket_epoch = (epoch // interval_s) * interval_s
    return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)


class _BarBuffer:
    """Mutable accumulator for an in-flight bar. Stays separate from the immutable
    Bar Pydantic model so we don't pay validation cost per tick."""

    __slots__ = (
        "instrument", "timeframe", "start", "open", "high", "low", "close",
        "volume", "trade_count", "vwap_num", "vwap_den",
    )

    def __init__(self, instrument: Instrument, timeframe: BarTimeframe, start: datetime, price: Decimal) -> None:
        self.instrument = instrument
        self.timeframe = timeframe
        self.start = start
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume: Decimal = Decimal("0")
        self.trade_count = 0
        self.vwap_num: Decimal = Decimal("0")  # Σ price·size
        self.vwap_den: Decimal = Decimal("0")  # Σ size

    def update_price(self, price: Decimal) -> None:
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price

    def add_trade(self, price: Decimal, size: Decimal) -> None:
        self.update_price(price)
        self.volume += size
        self.trade_count += 1
        self.vwap_num += price * size
        self.vwap_den += size

    def to_bar(self) -> Bar:
        vwap = (self.vwap_num / self.vwap_den) if self.vwap_den > 0 else None
        return Bar(
            instrument=self.instrument,
            timeframe=self.timeframe,
            timestamp=self.start,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            vwap=vwap,
            trade_count=self.trade_count,
        )


class TimeBarAggregator:
    """Wall-clock time bars at a single timeframe. Per-instrument state."""

    def __init__(self, timeframe: BarTimeframe) -> None:
        if timeframe not in _INTERVAL_SECONDS:
            raise ValueError(f"unsupported timeframe: {timeframe}")
        self._timeframe = timeframe
        self._interval_s = _INTERVAL_SECONDS[timeframe]
        self._buffers: dict[str, _BarBuffer] = {}

    @property
    def interval(self) -> timedelta:
        return timedelta(seconds=self._interval_s)

    def on_tick(self, tick: Tick) -> list[Bar]:
        # Determine the effective price: prefer last_price, fall back to mid.
        price = self._effective_price(tick)
        if price is None:
            return []  # no usable price (no last + no quotes); skip

        key = tick.instrument.key
        bucket = _bucket_start(tick.timestamp, self._interval_s)
        buf = self._buffers.get(key)

        closed: list[Bar] = []
        if buf is None:
            self._buffers[key] = _BarBuffer(tick.instrument, self._timeframe, bucket, price)
            buf = self._buffers[key]
        elif bucket != buf.start:
            # Emit old, start new. If the gap is > 1 interval, intermediate buckets
            # have no data — we don't synthesize empty bars (consumers can detect
            # gaps from timestamps).
            closed.append(buf.to_bar())
            self._buffers[key] = _BarBuffer(tick.instrument, self._timeframe, bucket, price)
            buf = self._buffers[key]

        if tick.last_price is not None and tick.last_size is not None:
            buf.add_trade(tick.last_price, tick.last_size)
        else:
            buf.update_price(price)

        return closed

    def on_bar(self, bar: Bar) -> list[Bar]:
        raise NotImplementedError("TimeBarAggregator is tick-driven; use on_tick")

    def flush(self) -> list[Bar]:
        out = [buf.to_bar() for buf in self._buffers.values()]
        self._buffers.clear()
        return out

    @staticmethod
    def _effective_price(tick: Tick) -> Decimal | None:
        if tick.last_price is not None:
            return tick.last_price
        if tick.bid is not None and tick.ask is not None:
            return (tick.bid + tick.ask) / Decimal("2")
        return tick.bid or tick.ask
