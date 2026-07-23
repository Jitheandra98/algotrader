"""Renko aggregator — fixed-brick-size price bars.

State per instrument is just the last brick's *close* and direction. A new brick
emits whenever price moves at least ``brick_size`` away from that close in the
same direction, OR at least ``2 * brick_size`` for a reversal (classic Renko).

Each brick has::

    open  = previous brick close
    close = open ± brick_size
    high  = max(open, close)
    low   = min(open, close)

Multiple bricks may emit from a single tick if the move is large.

Volume across a tick is allocated to bricks emitted on that tick *proportionally to
brick count* (i.e., volume/N each) — simple and deterministic. The first tick on a
new instrument seeds the price baseline and emits nothing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from core.schemas import Bar, BarTimeframe, Tick


_Direction = Literal[1, -1, 0]


class _RenkoState:
    __slots__ = ("last_close", "direction")

    def __init__(self, last_close: Decimal) -> None:
        self.last_close: Decimal = last_close
        self.direction: _Direction = 0


class RenkoAggregator:
    """Brick-size-based Renko."""

    def __init__(self, brick_size: Decimal, timeframe: BarTimeframe = BarTimeframe.M1) -> None:
        if brick_size <= 0:
            raise ValueError("brick_size must be positive")
        self._brick_size = brick_size
        self._timeframe = timeframe  # carried on emitted bars purely for downstream tagging
        self._state: dict[str, _RenkoState] = {}

    def on_tick(self, tick: Tick) -> list[Bar]:
        price = tick.last_price or tick.bid or tick.ask
        if price is None and tick.bid is not None and tick.ask is not None:
            price = (tick.bid + tick.ask) / Decimal("2")
        if price is None:
            return []

        key = tick.instrument.key
        state = self._state.get(key)
        if state is None:
            self._state[key] = _RenkoState(price)
            return []

        bricks: list[Bar] = []
        brick = self._brick_size
        size_each = tick.last_size or Decimal("0")

        while True:
            move_up = price - state.last_close
            move_dn = state.last_close - price

            if state.direction >= 0 and move_up >= brick:
                open_ = state.last_close
                close = open_ + brick
                bricks.append(self._brick(tick, open_, close))
                state.last_close = close
                state.direction = 1
            elif state.direction <= 0 and move_dn >= brick:
                open_ = state.last_close
                close = open_ - brick
                bricks.append(self._brick(tick, open_, close))
                state.last_close = close
                state.direction = -1
            elif state.direction == 1 and move_dn >= 2 * brick:
                # Reversal: skip one brick (gap of 2x), then continue downward.
                open_ = state.last_close - brick  # the gap-step
                close = open_ - brick
                bricks.append(self._brick(tick, open_, close))
                state.last_close = close
                state.direction = -1
            elif state.direction == -1 and move_up >= 2 * brick:
                open_ = state.last_close + brick
                close = open_ + brick
                bricks.append(self._brick(tick, open_, close))
                state.last_close = close
                state.direction = 1
            else:
                break

        # Distribute volume equally among bricks emitted on this tick.
        if bricks and size_each > 0:
            allocation = size_each / Decimal(len(bricks))
            bricks = [
                b.model_copy(update={"volume": allocation, "trade_count": 1})
                for b in bricks
            ]

        return bricks

    def on_bar(self, bar: Bar) -> list[Bar]:
        raise NotImplementedError("RenkoAggregator is tick-driven; use on_tick")

    def flush(self) -> list[Bar]:
        return []

    def _brick(self, tick: Tick, open_: Decimal, close: Decimal) -> Bar:
        high = max(open_, close)
        low = min(open_, close)
        return Bar(
            instrument=tick.instrument,
            timeframe=self._timeframe,
            timestamp=tick.timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=Decimal("0"),
            vwap=None,
            trade_count=0,
        )
