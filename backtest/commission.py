"""Commission models.

Models return a positive Decimal — the cost paid by the trader per fill. The
broker subtracts this from cash on each fill.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from core.schemas import Fill


class CommissionModel(ABC):
    @abstractmethod
    def commission(self, fill: Fill) -> Decimal:
        """Return the commission charged for this fill (always >= 0)."""


class ZeroCommission(CommissionModel):
    """For brokers like Alpaca on US equities."""

    def commission(self, fill: Fill) -> Decimal:  # noqa: ARG002
        return Decimal("0")


class PerShareCommission(CommissionModel):
    """IBKR-style per-share. ``per_share`` charged per unit, with optional
    per-fill ``minimum`` and a per-fill ``maximum_pct`` of fill value."""

    def __init__(
        self,
        per_share: Decimal | float | str = "0.005",
        minimum: Decimal | float | str = "1.00",
        maximum_pct: Decimal | float | str = "0.01",
    ) -> None:
        self._ps = Decimal(str(per_share))
        self._min = Decimal(str(minimum))
        self._max_pct = Decimal(str(maximum_pct))
        if self._ps < 0 or self._min < 0 or self._max_pct < 0:
            raise ValueError("all params must be >= 0")

    def commission(self, fill: Fill) -> Decimal:
        raw = self._ps * fill.quantity
        gross = fill.price * fill.quantity
        capped = min(raw, self._max_pct * gross) if self._max_pct > 0 else raw
        return max(capped, self._min)


class PerTradeCommission(CommissionModel):
    """Flat per-fill fee."""

    def __init__(self, per_trade: Decimal | float | str = "1.00") -> None:
        self._pt = Decimal(str(per_trade))
        if self._pt < 0:
            raise ValueError("per_trade must be >= 0")

    def commission(self, fill: Fill) -> Decimal:  # noqa: ARG002
        return self._pt


class PercentCommission(CommissionModel):
    """Crypto-style percentage of notional value. ``pct=0.001`` = 10 bps."""

    def __init__(self, pct: Decimal | float | str = "0.001") -> None:
        self._pct = Decimal(str(pct))
        if self._pct < 0:
            raise ValueError("pct must be >= 0")

    def commission(self, fill: Fill) -> Decimal:
        return self._pct * fill.price * fill.quantity
