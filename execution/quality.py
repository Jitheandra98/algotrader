"""Fill quality tracking.

Records per-fill latency (submit → ack → first-fill → done) and realized slippage
(fill price vs the reference price at submit). Aggregates into per-(strategy,
instrument, window) statistics used by the operator dashboard (M8) and the ML
overlay (M9).

Reference price for slippage:
  - Market BUY: ask at submit (or mid if no quote)
  - Market SELL: bid at submit (or mid)
  - Limit: the limit price itself
  - Stop: the stop price (after trigger)
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from core.schemas import Fill, Order, OrderSide, OrderType


@dataclass(frozen=True, slots=True)
class FillRecord:
    order_id: UUID
    instrument_key: str
    strategy_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    reference_price: Decimal | None
    submitted_at: datetime
    acked_at: datetime | None
    filled_at: datetime
    venue: str = "unknown"

    @property
    def slippage_per_unit(self) -> Decimal | None:
        if self.reference_price is None:
            return None
        # Always express as adverse (positive number = bad fill).
        sign = Decimal("1") if self.side is OrderSide.BUY else Decimal("-1")
        return sign * (self.price - self.reference_price)

    @property
    def total_slippage(self) -> Decimal | None:
        per = self.slippage_per_unit
        return per * self.quantity if per is not None else None

    @property
    def ack_latency_ms(self) -> float | None:
        if self.acked_at is None:
            return None
        return (self.acked_at - self.submitted_at).total_seconds() * 1000

    @property
    def fill_latency_ms(self) -> float:
        return (self.filled_at - self.submitted_at).total_seconds() * 1000


@dataclass(frozen=True, slots=True)
class QualityStats:
    count: int
    avg_slippage_per_unit: float
    total_slippage: float
    median_fill_latency_ms: float
    p95_fill_latency_ms: float
    avg_fill_latency_ms: float
    median_ack_latency_ms: float | None


class FillQualityTracker:
    """In-memory ring buffer of recent fills with aggregation helpers."""

    def __init__(self, *, capacity: int = 10_000) -> None:
        self._records: deque[FillRecord] = deque(maxlen=capacity)
        # Submit timestamps keyed by order id so we can compute latency on fill.
        self._submit_index: dict[UUID, tuple[datetime, Decimal | None]] = {}
        self._ack_index: dict[UUID, datetime] = {}

    def on_submit(self, order: Order, reference_price: Decimal | None = None) -> None:
        self._submit_index[order.id] = (datetime.now(tz=order.created_at.tzinfo), reference_price)

    def on_ack(self, order_id: UUID, ts: datetime) -> None:
        self._ack_index[order_id] = ts

    def on_fill(self, fill: Fill, strategy_id: str, venue: str = "unknown") -> FillRecord:
        submitted, ref_price = self._submit_index.get(fill.order_id, (fill.timestamp, None))
        acked = self._ack_index.get(fill.order_id)
        record = FillRecord(
            order_id=fill.order_id,
            instrument_key=fill.instrument.key,
            strategy_id=strategy_id,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            reference_price=ref_price,
            submitted_at=submitted,
            acked_at=acked,
            filled_at=fill.timestamp,
            venue=venue,
        )
        self._records.append(record)
        return record

    # ---- aggregation ----

    def records(self) -> list[FillRecord]:
        return list(self._records)

    def stats(
        self,
        *,
        strategy_id: str | None = None,
        instrument_key: str | None = None,
        window: timedelta | None = None,
    ) -> QualityStats | None:
        now = datetime.now()
        cutoff = now - window if window is not None else None
        records = [
            r
            for r in self._records
            if (strategy_id is None or r.strategy_id == strategy_id)
            and (instrument_key is None or r.instrument_key == instrument_key)
            and (cutoff is None or r.filled_at.replace(tzinfo=None) >= cutoff.replace(tzinfo=None))
        ]
        if not records:
            return None
        slip_per_unit = [float(r.slippage_per_unit) for r in records if r.slippage_per_unit is not None]
        slip_total = [float(r.total_slippage) for r in records if r.total_slippage is not None]
        fill_lats = [r.fill_latency_ms for r in records]
        ack_lats = [r.ack_latency_ms for r in records if r.ack_latency_ms is not None]
        return QualityStats(
            count=len(records),
            avg_slippage_per_unit=statistics.fmean(slip_per_unit) if slip_per_unit else 0.0,
            total_slippage=sum(slip_total),
            median_fill_latency_ms=statistics.median(fill_lats),
            p95_fill_latency_ms=_pct(fill_lats, 0.95),
            avg_fill_latency_ms=statistics.fmean(fill_lats),
            median_ack_latency_ms=statistics.median(ack_lats) if ack_lats else None,
        )


def _pct(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = pct * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def reference_price_for(order: Order, *, bid: Decimal | None, ask: Decimal | None, mid: Decimal | None) -> Decimal | None:
    """Choose the reference price for slippage attribution."""
    if order.order_type in (OrderType.LIMIT, OrderType.LOC, OrderType.STOP_LIMIT):
        return order.limit_price
    if order.order_type in (OrderType.STOP, OrderType.TRAILING_STOP):
        return order.stop_price
    # Market / MOC.
    if order.side is OrderSide.BUY:
        return ask or mid or bid
    return bid or mid or ask
