"""Slippage models.

Slippage is the difference between the price a strategy expects and the price the
fill actually executes at. In live trading it's driven by spread, latency, and
adverse selection; in backtest we model it deterministically so the backtest is
*pessimistic enough* to not overpromise.

Conventions:
  - Slippage is always **adverse** to the trader. Long fills are higher; short
    fills are lower. The model returns a positive ``slip_per_unit`` and the broker
    applies the sign based on side.
  - Models are stateless except :class:`AtrSlippage`, which needs ATR per
    instrument (the broker injects it).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from core.schemas import Instrument, OrderSide


class SlippageModel(ABC):
    """Compute adverse slippage in price units."""

    @abstractmethod
    def slip(
        self,
        *,
        instrument: Instrument,
        side: OrderSide,
        reference_price: Decimal,
        quantity: Decimal,
        atr: Decimal | None = None,
    ) -> Decimal:
        """Return the *signed* slippage to add to the reference price. Long fills
        get a positive number; short fills get a negative one."""


class ZeroSlippage(SlippageModel):
    def slip(self, *, instrument, side, reference_price, quantity, atr=None) -> Decimal:  # noqa: ARG002
        return Decimal("0")


class PercentSlippage(SlippageModel):
    """Reference price times a percentage. ``pct=0.01`` = 1%."""

    def __init__(self, pct: Decimal | float | str) -> None:
        self._pct = Decimal(str(pct))
        if self._pct < 0:
            raise ValueError("pct must be >= 0")

    def slip(self, *, instrument, side, reference_price, quantity, atr=None) -> Decimal:  # noqa: ARG002
        magnitude = reference_price * self._pct
        return magnitude if side is OrderSide.BUY else -magnitude


class BpsSlippage(SlippageModel):
    """Basis points of the reference price. ``bps=5`` = 0.05%."""

    def __init__(self, bps: Decimal | float | str) -> None:
        self._bps = Decimal(str(bps))
        if self._bps < 0:
            raise ValueError("bps must be >= 0")

    def slip(self, *, instrument, side, reference_price, quantity, atr=None) -> Decimal:  # noqa: ARG002
        magnitude = reference_price * self._bps / Decimal("10000")
        return magnitude if side is OrderSide.BUY else -magnitude


class AtrSlippage(SlippageModel):
    """ATR-scaled. ``factor=0.1`` means slippage = 0.1 × ATR.

    If ATR is not available (warmup, no recent bars), falls back to a small
    ``fallback_bps`` of the reference price. This matters for the first few bars
    of a backtest before ATR warms up.
    """

    def __init__(self, factor: Decimal | float | str = "0.1", fallback_bps: Decimal | float | str = "5") -> None:
        self._factor = Decimal(str(factor))
        self._fallback_bps = Decimal(str(fallback_bps))
        if self._factor < 0 or self._fallback_bps < 0:
            raise ValueError("factor and fallback_bps must be >= 0")

    def slip(self, *, instrument, side, reference_price, quantity, atr=None) -> Decimal:  # noqa: ARG002
        if atr is None or atr <= 0:
            magnitude = reference_price * self._fallback_bps / Decimal("10000")
        else:
            magnitude = atr * self._factor
        return magnitude if side is OrderSide.BUY else -magnitude
