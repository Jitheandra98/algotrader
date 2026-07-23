"""Risk layer base types.

The risk engine sits between strategy signal emission and broker order
submission. **No strategy can bypass it.** Per the brief:

  "Risk management must sit between strategies and order execution — no strategy
   can bypass it."

The engine takes a :class:`Signal`, runs it through a :class:`Sizer` to compute a
candidate :class:`Order`, then runs the order through every configured
:class:`RiskLimit` in sequence. Any limit can reject. The :class:`RiskDecision`
output carries the allowed/blocked verdict plus every triggered rejection's
rationale (we keep them all for audit).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from core.schemas import Bar, Direction, Instrument, Order, Position, Signal


@dataclass(frozen=True, slots=True)
class RiskRejection:
    """One limit's reason for blocking a trade. Multiple may apply to the same
    signal — the engine keeps them all for the audit log."""

    limit: str          # registry-style name of the limit, e.g. "max_position_size"
    reason: str         # human-readable explanation
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """The outcome of running a signal through the risk engine."""

    signal: Signal
    allowed: bool
    order: Order | None
    rejections: tuple[RiskRejection, ...] = ()
    sizing_meta: dict[str, Any] = field(default_factory=dict)
    decided_at: datetime | None = None

    @property
    def is_blocked(self) -> bool:
        return not self.allowed


class RiskContext(Protocol):
    """Read-only view the risk engine uses to make decisions. The backtest
    engine and the live engine both implement this — same risk code in both."""

    @property
    def now(self) -> datetime: ...

    @property
    def cash(self) -> Decimal: ...

    @property
    def equity(self) -> Decimal: ...

    @property
    def account_value(self) -> Decimal: ...

    @property
    def is_margin_account(self) -> bool: ...

    def positions(self) -> list[Position]: ...

    def position(self, instrument: Instrument) -> Position | None: ...

    def last_bar(self, instrument: Instrument) -> Bar | None: ...

    def atr(self, instrument: Instrument) -> Decimal | None: ...

    def recent_trade_pnls(self, n: int) -> list[Decimal]:
        """Most-recent N closed-trade P&Ls. Used by the kill-switch."""
        ...

    def realized_pnl_since(self, since: datetime) -> Decimal:
        """Sum of closed-trade P&L since ``since`` (e.g., start of day)."""
        ...

    def equity_at(self, ts: datetime) -> Decimal | None:
        """Equity value at or before ``ts``. None if no data point exists."""
        ...

    def is_ssr_flagged(self, instrument: Instrument) -> bool: ...

    def sector_of(self, instrument: Instrument) -> str | None: ...

    def correlation(self, a: Instrument, b: Instrument) -> float | None:
        """Pairwise correlation in [-1, 1], or None if not enough data."""
        ...


# ----- Sizer -----


class Sizer(ABC):
    """Decide the **quantity** to trade based on the signal and current state.

    Sizers do not check limits — they propose a quantity. The limits are checked
    by :class:`RiskLimit` instances. Sizers may return ``Decimal("0")`` to indicate
    "no trade" (e.g., insufficient information).
    """

    name: str = "sizer"

    @abstractmethod
    def size(self, signal: Signal, ctx: RiskContext) -> tuple[Decimal, dict[str, Any]]:
        """Return ``(quantity, metadata)``. Metadata becomes ``sizing_meta`` on the
        decision and is persisted in the audit log."""


# ----- Limit -----


class RiskLimit(ABC):
    """Veto power over a proposed order. Returns ``None`` to allow, or a
    :class:`RiskRejection` to block."""

    name: str = "limit"

    @abstractmethod
    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None: ...

    def update_on_fill(self, fill, ctx: RiskContext) -> None:  # noqa: ANN001, ARG002
        """Optional hook — called after each fill so stateful limits (PDT, kill
        switch, daily-DD watermarks) can update their counters."""
        return None
