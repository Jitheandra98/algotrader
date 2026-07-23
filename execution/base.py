"""Broker adapter ABC + execution-layer value types.

Every live or paper broker implements :class:`BrokerAdapter`. The interface is
async — live brokers do real network I/O, paper brokers can implement it as
in-process simulation.

The simulated broker used in backtests (:mod:`backtest.broker`) deliberately
does NOT implement this interface: the backtest loop is synchronous and drives
the broker explicitly via ``match(bar)``. The execution layer's smart router
and live engine operate against this ABC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from core.schemas import Fill, Order, OrderStatus, Position


# ---------- account / execution reports ----------


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """Broker-reported account state (the broker is canonical, not us)."""

    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    is_margin_account: bool
    is_pdt_flagged: bool = False
    is_pattern_day_trader: bool = False
    daytrading_buying_power: Decimal | None = None
    multiplier: Decimal = Decimal("1")
    currency: str = "USD"
    raw: dict[str, Any] = field(default_factory=dict)


class ExecutionEvent(str, Enum):
    """The kinds of broker-side order lifecycle events we relay."""

    ACK = "ACK"                 # broker accepted the order
    REJECT = "REJECT"           # broker refused (margin, bad symbol, etc.)
    NEW = "NEW"                 # working in the book
    PARTIAL_FILL = "PARTIAL_FILL"
    FILL = "FILL"
    CANCEL = "CANCEL"
    EXPIRE = "EXPIRE"
    REPLACE = "REPLACE"


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Broker push event for an order. Carries the most recent state plus any
    associated fill."""

    event: ExecutionEvent
    timestamp: datetime
    order_id: UUID
    broker_order_id: str | None = None
    status: OrderStatus | None = None
    fill: Fill | None = None
    leaves_qty: Decimal | None = None
    reject_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ---------- the adapter ----------


class BrokerAdapter(ABC):
    """Broker-agnostic contract. Live (Alpaca, IBKR, etc.) brokers implement this.

    Lifecycle: ``connect`` → ``submit`` / ``cancel`` / ``positions`` while open →
    ``close``. ``stream_order_updates`` returns events as they arrive; the live
    engine consumes them and applies fills to the portfolio.

    Idempotency: ``submit(order)`` is idempotent on ``order.client_order_id`` —
    re-submitting the same id MUST NOT create a duplicate. The broker may
    return the previously submitted order.
    """

    name: str

    @abstractmethod
    async def connect(self) -> None:
        """Establish REST + streaming connections. Idempotent."""

    @abstractmethod
    async def close(self) -> None:
        """Close all connections. Idempotent."""

    @abstractmethod
    async def submit(self, order: Order) -> Order:
        """Send an order. Returns the order updated with broker-assigned id and
        status. Idempotent on client_order_id."""

    @abstractmethod
    async def cancel(self, order_id: UUID) -> bool:
        """Request cancel. Returns True if the cancel was acknowledged (the order
        may still fill before the cancel takes effect)."""

    async def modify(self, order_id: UUID, **fields: Any) -> Order:  # noqa: ARG002
        """Replace an order's params (limit_price / stop_price / quantity).
        Default implementation raises — brokers that support it override."""
        raise NotImplementedError(f"{self.name} does not support modify")

    @abstractmethod
    async def get_order(self, order_id: UUID) -> Order | None: ...

    @abstractmethod
    async def positions(self) -> list[Position]: ...

    @abstractmethod
    async def account(self) -> AccountInfo: ...

    @abstractmethod
    def stream_order_updates(self) -> AsyncIterator[ExecutionReport]:
        """Async iterator over broker-side order lifecycle events. Implementations
        reconnect internally."""
        ...
