"""Leverage limits."""

from __future__ import annotations

from decimal import Decimal

from core.schemas import Order, OrderSide, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


class GrossLeverage(RiskLimit):
    """Cap on Σ|position value| / equity. Counts longs and shorts the same way."""

    name = "gross_leverage"

    def __init__(self, max_leverage: Decimal | float | str = "1.0") -> None:
        self._max = Decimal(str(max_leverage))
        if self._max <= 0:
            raise ValueError("max_leverage must be > 0")

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        existing = Decimal("0")
        for pos in ctx.positions():
            price = pos.current_price or pos.average_price
            existing += abs(pos.quantity) * price
        added = bar.close * proposed.quantity
        total = existing + added
        ratio = total / ctx.equity if ctx.equity > 0 else Decimal("Inf")
        if ratio > self._max:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"gross leverage {ratio:.2f}x > cap {self._max}x "
                    f"(positions ${total:.2f} / equity ${ctx.equity:.2f})"
                ),
                metadata={"ratio": str(ratio), "cap": str(self._max)},
            )
        return None


class NetLeverage(RiskLimit):
    """Cap on (Σ long values - Σ short values) / equity. Signed."""

    name = "net_leverage"

    def __init__(self, max_long: Decimal | float | str = "1.0", max_short: Decimal | float | str = "1.0") -> None:
        self._max_long = Decimal(str(max_long))
        self._max_short = Decimal(str(max_short))

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        net = Decimal("0")
        for pos in ctx.positions():
            price = pos.current_price or pos.average_price
            net += pos.quantity * price  # signed
        sign = Decimal("1") if proposed.side is OrderSide.BUY else Decimal("-1")
        net = net + sign * bar.close * proposed.quantity

        if ctx.equity <= 0:
            return None  # liquidation territory; let other limits handle it
        ratio = net / ctx.equity
        if ratio > self._max_long:
            return RiskRejection(
                limit=self.name,
                reason=f"net long leverage {ratio:.2f}x > cap {self._max_long}x",
                metadata={"net": str(net), "ratio": str(ratio)},
            )
        if ratio < -self._max_short:
            return RiskRejection(
                limit=self.name,
                reason=f"net short leverage {ratio:.2f}x > cap {self._max_short}x",
                metadata={"net": str(net), "ratio": str(ratio)},
            )
        return None
