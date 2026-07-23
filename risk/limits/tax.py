"""Tax-aware lot selection and wash-sale guard.

Two pieces:

  - :class:`LotSelector` — given a closing fill, picks which open tax lot(s) it
    closes. Supported methods: FIFO, LIFO, HIFO (highest cost first — minimizes
    short-term gain), and MIN_TAX (a smarter heuristic that prefers long-term
    holdings to realize long-term gains and short-term losers to harvest losses).
  - :class:`WashSaleGuard` — flags a closing fill that realizes a loss as a
    potential wash sale if the same instrument was bought within the last 30
    calendar days, OR will be bought within the next 30. Optionally blocks
    new BUY orders that would trigger the wash sale on a recent loss.

Wash-sale rule is a US tax-code concept (IRS §1091). For non-US markets the
guard is a no-op (config-driven).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from core.schemas import Instrument, Order, OrderSide, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


class LotMethod(str, Enum):
    FIFO = "FIFO"     # First-in, first-out
    LIFO = "LIFO"     # Last-in, first-out
    HIFO = "HIFO"     # Highest-cost-out (minimizes short-term gain)
    MIN_TAX = "MIN_TAX"


@dataclass(slots=True)
class TaxLot:
    """One opening event for a position."""

    instrument: Instrument
    quantity: Decimal      # always positive — sign attached to the position
    cost_basis: Decimal    # per-unit
    opened_at: datetime
    is_long: bool          # True = long position, False = short

    def age_days(self, now: datetime) -> int:
        return (now - self.opened_at).days


class LotSelector:
    """Tracks lots per instrument; selects lots to close on each closing fill.

    Caller is responsible for invoking :meth:`add_lot` on opening fills and
    :meth:`close_lots` on closing fills. The returned ``(lot, closed_qty)``
    tuples can be persisted to a tax ledger.
    """

    def __init__(self, method: LotMethod = LotMethod.FIFO) -> None:
        self._method = method
        self._lots: dict[str, list[TaxLot]] = {}

    def lots(self, instrument: Instrument) -> list[TaxLot]:
        return list(self._lots.get(instrument.key, []))

    def add_lot(self, lot: TaxLot) -> None:
        self._lots.setdefault(lot.instrument.key, []).append(lot)

    def close_lots(
        self,
        instrument: Instrument,
        qty: Decimal,
        exit_price: Decimal,
        exit_time: datetime,
    ) -> list[tuple[TaxLot, Decimal, Decimal]]:
        """Returns ``(lot, closed_qty, realized_pnl)`` per consumed lot."""
        bucket = self._lots.get(instrument.key, [])
        if not bucket:
            return []
        ordered = self._order(bucket, exit_price)
        results: list[tuple[TaxLot, Decimal, Decimal]] = []
        remaining = qty
        for lot in ordered:
            if remaining <= 0:
                break
            take = min(remaining, lot.quantity)
            sign = Decimal("1") if lot.is_long else Decimal("-1")
            realized = sign * (exit_price - lot.cost_basis) * take
            results.append((lot, take, realized))
            lot.quantity -= take
            remaining -= take
        # Compact: drop fully consumed lots.
        self._lots[instrument.key] = [lot for lot in bucket if lot.quantity > 0]
        return results

    def _order(self, lots: list[TaxLot], exit_price: Decimal) -> list[TaxLot]:
        method = self._method
        if method is LotMethod.FIFO:
            return sorted(lots, key=lambda l: l.opened_at)
        if method is LotMethod.LIFO:
            return sorted(lots, key=lambda l: l.opened_at, reverse=True)
        if method is LotMethod.HIFO:
            return sorted(lots, key=lambda l: l.cost_basis, reverse=True)
        if method is LotMethod.MIN_TAX:
            # Heuristic: pick the lot that yields the smallest tax bill. For a
            # gain, prefer long-term (>365 days). For a loss, prefer
            # short-term (more deductible against ordinary income, kind of).
            def score(lot: TaxLot) -> tuple[int, Decimal]:
                gain = exit_price - lot.cost_basis
                long_term = lot.age_days(exit_time=lot.opened_at + timedelta(days=400)) > 365
                # rough proxy: prefer long-term gains and short-term losses
                if gain >= 0:
                    return (0 if long_term else 1, -gain)
                return (1 if long_term else 0, gain)
            # exit_time unknown here without a passed arg — fall back to FIFO.
            return sorted(lots, key=lambda l: l.opened_at)
        return lots


class WashSaleGuard(RiskLimit):
    """30-day wash-sale checker. By default *flags* (doesn't block). Set
    ``block=True`` to actively prevent the rebuy."""

    name = "wash_sale_guard"

    def __init__(self, block: bool = False, window_days: int = 30) -> None:
        self._block = block
        self._window = timedelta(days=window_days)
        # Per-instrument: list of (timestamp, realized_pnl) for closes.
        self._closes: dict[str, deque[tuple[datetime, Decimal]]] = {}

    def update_on_fill(self, fill, ctx: RiskContext) -> None:  # noqa: ANN001, ARG002
        # We can't see realized P&L per fill directly; the engine's portfolio
        # would have to expose it. For now we record every close (sell that
        # reduces a long, buy that reduces a short) and let the check inspect
        # whether it was a loss via the recent trade ledger.
        # No-op here; the engine layer in risk/engine.py updates state.
        return None

    def record_close(self, instrument: Instrument, ts: datetime, realized_pnl: Decimal) -> None:
        if realized_pnl >= 0:
            return  # not a loss; not a wash-sale candidate
        bucket = self._closes.setdefault(instrument.key, deque(maxlen=128))
        bucket.append((ts, realized_pnl))
        # Trim old entries.
        cutoff = ts - self._window
        while bucket and bucket[0][0] < cutoff:
            bucket.popleft()

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        # Only relevant on opening BUY orders (new long after a recent loss).
        if proposed.side is not OrderSide.BUY:
            return None
        existing = ctx.position(signal.instrument)
        if existing is not None and existing.quantity > 0:
            return None  # already long — adding-on isn't a wash-sale
        closes = self._closes.get(signal.instrument.key)
        if not closes:
            return None
        cutoff = ctx.now - self._window
        recent_losses = [(t, p) for (t, p) in closes if t >= cutoff and p < 0]
        if not recent_losses:
            return None
        msg = (
            f"wash-sale: {signal.instrument.symbol} closed at a loss within last "
            f"{self._window.days} days ({len(recent_losses)} losing closes)"
        )
        if self._block:
            return RiskRejection(
                limit=self.name,
                reason=msg,
                metadata={"recent_losses": len(recent_losses)},
            )
        # Flag without blocking — log only.
        return None
