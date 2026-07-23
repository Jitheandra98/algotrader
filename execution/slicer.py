"""Order slicing strategies.

A :class:`Slicer` takes a parent order and returns a *plan* — a list of child
orders to submit, possibly spread over time. The router calls
:meth:`Slicer.plan` to get the schedule, then orchestrates submission.

Shipped slicers:
  - :class:`SimpleSplitSlicer` — N equal-sized child orders, all submitted now.
  - :class:`TwapSlicer` — N equal-sized child orders, spaced ``interval`` apart.
  - :class:`IcebergSlicer` — submit only ``visible_qty`` at a time; refill on fill.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from core.schemas import Order


@dataclass(frozen=True, slots=True)
class ChildOrder:
    """A child slice with a scheduled release time. ``release_at`` may be
    ``None`` to mean 'submit immediately'."""

    order: Order
    release_at: datetime | None = None


class Slicer(ABC):
    """Strategy for breaking a parent order into children."""

    name: str = "slicer"

    @abstractmethod
    def plan(self, parent: Order, *, now: datetime) -> list[ChildOrder]:
        """Return the list of children. Implementations may return one child
        equal to the parent (a no-op slice) when the parent is small."""


class SimpleSplitSlicer(Slicer):
    """Split into ``n`` equal-sized children submitted simultaneously. Useful
    when the broker has a per-order size cap."""

    name = "simple_split"

    def __init__(self, n: int = 4, min_child_qty: Decimal | float | str = "1") -> None:
        if n < 1:
            raise ValueError("n must be >= 1")
        self._n = n
        self._min = Decimal(str(min_child_qty))

    def plan(self, parent: Order, *, now: datetime) -> list[ChildOrder]:
        per = (parent.quantity / Decimal(self._n)).quantize(Decimal("0.0001"))
        if per < self._min:
            # Parent too small to split — submit as-is.
            return [ChildOrder(order=parent, release_at=now)]
        # Distribute rounding error onto the last child.
        children: list[ChildOrder] = []
        running = Decimal("0")
        for i in range(self._n):
            qty = per if i < self._n - 1 else (parent.quantity - running)
            running += qty
            child = parent.model_copy(
                update={
                    "id": parent.id if i == 0 else _new_uuid(),
                    "client_order_id": f"{parent.client_order_id}.s{i}",
                    "quantity": qty,
                    "parent_order_id": parent.id if i > 0 else None,
                }
            )
            children.append(ChildOrder(order=child, release_at=now))
        return children


class TwapSlicer(Slicer):
    """N equal-sized children submitted ``interval`` apart (TWAP)."""

    name = "twap"

    def __init__(self, n: int = 6, interval: timedelta = timedelta(seconds=10), min_child_qty: Decimal | float | str = "1") -> None:
        if n < 1:
            raise ValueError("n must be >= 1")
        if interval.total_seconds() <= 0:
            raise ValueError("interval must be positive")
        self._n = n
        self._interval = interval
        self._min = Decimal(str(min_child_qty))

    def plan(self, parent: Order, *, now: datetime) -> list[ChildOrder]:
        per = (parent.quantity / Decimal(self._n)).quantize(Decimal("0.0001"))
        if per < self._min:
            return [ChildOrder(order=parent, release_at=now)]
        children: list[ChildOrder] = []
        running = Decimal("0")
        for i in range(self._n):
            qty = per if i < self._n - 1 else (parent.quantity - running)
            running += qty
            child = parent.model_copy(
                update={
                    "id": parent.id if i == 0 else _new_uuid(),
                    "client_order_id": f"{parent.client_order_id}.t{i}",
                    "quantity": qty,
                    "parent_order_id": parent.id if i > 0 else None,
                }
            )
            children.append(ChildOrder(order=child, release_at=now + i * self._interval))
        return children


class IcebergSlicer(Slicer):
    """Show only ``visible_qty`` at a time. The router submits each next slice
    after the previous one fills (or partially fills past a threshold)."""

    name = "iceberg"

    def __init__(self, visible_qty: Decimal | float | str = "100") -> None:
        self._visible = Decimal(str(visible_qty))
        if self._visible <= 0:
            raise ValueError("visible_qty must be > 0")

    def plan(self, parent: Order, *, now: datetime) -> list[ChildOrder]:
        # The first child only — the router refills as fills come in.
        first_qty = min(parent.quantity, self._visible)
        child = parent.model_copy(
            update={
                "id": parent.id,
                "client_order_id": f"{parent.client_order_id}.i0",
                "quantity": first_qty,
            }
        )
        return [ChildOrder(order=child, release_at=now)]

    @property
    def visible_qty(self) -> Decimal:
        return self._visible


def _new_uuid():
    from uuid import uuid4
    return uuid4()
