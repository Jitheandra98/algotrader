"""Portfolio — positions, cash, P&L, equity curve, trade ledger.

This is the canonical "what do I own and what's it worth" tracker. Fills come in,
positions update, cash moves, P&L accrues. Bar updates mark-to-market.

Trade ledger: a *trade* is an opening event paired with a closing event for the
same instrument and side. Realized P&L per trade feeds the KPI module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from core.logging import get_logger
from core.schemas import Bar, Fill, Instrument, OrderSide, Position

log = get_logger(__name__)


@dataclass
class _OpenPosition:
    """Mutable position state. We don't roundtrip via the immutable Pydantic
    Position model on every fill — the hot path uses this dataclass and we
    materialize a Position snapshot on demand."""

    instrument: Instrument
    quantity: Decimal  # signed
    average_price: Decimal  # cost basis (BUY → buy avg; SELL/short → sell avg)
    realized_pnl: Decimal = Decimal("0")
    opened_at: datetime = field(default_factory=lambda: datetime.fromtimestamp(0))
    strategy_id: str | None = None


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """A completed trade — used by KPIs (win rate, profit factor, expectancy)."""

    instrument: Instrument
    side: str  # 'LONG' or 'SHORT' — side of the position that was closed
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal           # net of commissions allocated to this trade
    commission: Decimal
    strategy_id: str | None = None


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    cash: Decimal
    positions_value: Decimal
    equity: Decimal


class Portfolio:
    """Single-currency, multi-instrument portfolio."""

    def __init__(self, initial_cash: Decimal | float | str = "100000") -> None:
        self._initial_cash = Decimal(str(initial_cash))
        self._cash: Decimal = self._initial_cash
        self._positions: dict[str, _OpenPosition] = {}
        self._last_prices: dict[str, Decimal] = {}
        self._equity_curve: list[EquityPoint] = []
        self._trades: list[TradeRecord] = []

    # ---- accessors ----

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def initial_cash(self) -> Decimal:
        return self._initial_cash

    @property
    def positions(self) -> list[Position]:
        return [self._materialize(p) for p in self._positions.values()]

    @property
    def open_position_count(self) -> int:
        return len(self._positions)

    @property
    def equity_curve(self) -> list[EquityPoint]:
        return list(self._equity_curve)

    @property
    def trades(self) -> list[TradeRecord]:
        return list(self._trades)

    def position(self, instrument: Instrument) -> Position | None:
        p = self._positions.get(instrument.key)
        return self._materialize(p) if p else None

    def equity(self) -> Decimal:
        return self._cash + self._positions_value()

    def _positions_value(self) -> Decimal:
        total = Decimal("0")
        for p in self._positions.values():
            mark = self._last_prices.get(p.instrument.key, p.average_price)
            total += p.quantity * mark
        return total

    def _materialize(self, p: _OpenPosition) -> Position:
        mark = self._last_prices.get(p.instrument.key, p.average_price)
        unrealized = (mark - p.average_price) * p.quantity
        return Position(
            instrument=p.instrument,
            quantity=p.quantity,
            average_price=p.average_price,
            current_price=mark,
            unrealized_pnl=unrealized,
            realized_pnl=p.realized_pnl,
            strategy_id=p.strategy_id,
            opened_at=p.opened_at,
            last_updated=p.opened_at,
        )

    # ---- fills ----

    def apply_fill(self, fill: Fill, *, strategy_id: str | None = None) -> TradeRecord | None:
        """Apply a fill. Returns a TradeRecord if a position was closed or reduced."""
        key = fill.instrument.key
        side_sign = Decimal("1") if fill.side is OrderSide.BUY else Decimal("-1")
        fill_signed_qty = side_sign * fill.quantity

        # Cash flow: BUY → -money, SELL → +money. Commission is always a cost.
        self._cash += -side_sign * fill.price * fill.quantity
        self._cash -= fill.commission

        existing = self._positions.get(key)

        if existing is None:
            # New position.
            self._positions[key] = _OpenPosition(
                instrument=fill.instrument,
                quantity=fill_signed_qty,
                average_price=fill.price,
                opened_at=fill.timestamp,
                strategy_id=strategy_id,
            )
            return None

        same_direction = (existing.quantity > 0 and fill_signed_qty > 0) or (
            existing.quantity < 0 and fill_signed_qty < 0
        )
        new_qty = existing.quantity + fill_signed_qty

        if same_direction:
            # Add to existing — weighted average cost.
            total_cost = abs(existing.quantity) * existing.average_price + abs(fill_signed_qty) * fill.price
            existing.average_price = total_cost / abs(new_qty)
            existing.quantity = new_qty
            return None

        # Opposite direction → closes or reduces (or reverses).
        existing_side = "LONG" if existing.quantity > 0 else "SHORT"
        side_factor = Decimal("1") if existing.quantity > 0 else Decimal("-1")

        if new_qty == 0 or (new_qty > 0) != (existing.quantity > 0):
            # Fully closed or reversed.
            closed_qty = abs(existing.quantity)
            pnl_per_unit = side_factor * (fill.price - existing.average_price)
            realized = pnl_per_unit * closed_qty
            # Allocate commission proportionally; if reversal, allocate by closed share.
            comm_alloc = fill.commission * (closed_qty / fill.quantity) if fill.quantity > 0 else Decimal("0")
            trade = TradeRecord(
                instrument=fill.instrument,
                side=existing_side,
                entry_time=existing.opened_at,
                exit_time=fill.timestamp,
                entry_price=existing.average_price,
                exit_price=fill.price,
                quantity=closed_qty,
                pnl=realized - comm_alloc,
                commission=comm_alloc,
                strategy_id=strategy_id or existing.strategy_id,
            )
            self._trades.append(trade)
            existing.realized_pnl += realized

            if new_qty == 0:
                self._positions.pop(key)
            else:
                # Reversal: open opposite with remaining.
                self._positions[key] = _OpenPosition(
                    instrument=fill.instrument,
                    quantity=new_qty,
                    average_price=fill.price,
                    opened_at=fill.timestamp,
                    strategy_id=strategy_id or existing.strategy_id,
                )
            return trade

        # Partial reduction in same side.
        closed_qty = abs(fill_signed_qty)
        pnl_per_unit = side_factor * (fill.price - existing.average_price)
        realized = pnl_per_unit * closed_qty
        existing.realized_pnl += realized
        existing.quantity = new_qty
        trade = TradeRecord(
            instrument=fill.instrument,
            side=existing_side,
            entry_time=existing.opened_at,
            exit_time=fill.timestamp,
            entry_price=existing.average_price,
            exit_price=fill.price,
            quantity=closed_qty,
            pnl=realized - fill.commission,
            commission=fill.commission,
            strategy_id=strategy_id or existing.strategy_id,
        )
        self._trades.append(trade)
        return trade

    # ---- mark-to-market ----

    def mark_bar(self, bar: Bar) -> None:
        """Update the last-known price for the instrument; appends an equity point."""
        self._last_prices[bar.instrument.key] = bar.close
        self._equity_curve.append(
            EquityPoint(
                timestamp=bar.timestamp,
                cash=self._cash,
                positions_value=self._positions_value(),
                equity=self.equity(),
            )
        )

    def mark_prices(self, ts: datetime, prices: Iterable[tuple[Instrument, Decimal]]) -> None:
        """Bulk update last prices (used when multiple instruments tick on the same
        timestamp; appends one equity point)."""
        for inst, price in prices:
            self._last_prices[inst.key] = price
        self._equity_curve.append(
            EquityPoint(
                timestamp=ts,
                cash=self._cash,
                positions_value=self._positions_value(),
                equity=self.equity(),
            )
        )
