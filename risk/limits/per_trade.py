"""Per-trade risk caps."""

from __future__ import annotations

from decimal import Decimal

from core.schemas import Order, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


class PerTradeRiskCap(RiskLimit):
    """Hard cap on $-at-risk per trade. Computed as ``|entry - stop| × qty``.

    If the signal has no stop, this limit is a no-op for that signal (the sizer's
    fallback path applies instead). Use :class:`MaxPositionSize` as a backstop.

    Default 1% of equity matches the brief's "0.5% target, 1% absolute max"
    guidance — the sizer aims for 0.5% so most trades fit; this cap catches
    bugs where sizing is off.
    """

    name = "per_trade_risk_cap"

    def __init__(self, max_pct: Decimal | float | str = "0.01") -> None:
        self._max_pct = Decimal(str(max_pct))
        if self._max_pct <= 0 or self._max_pct > Decimal("0.10"):
            raise ValueError("per-trade risk cap must be in (0, 0.10]")

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        if signal.stop_price is None:
            return None
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        entry = bar.close
        risk_per_unit = abs(entry - signal.stop_price)
        risk_total = risk_per_unit * proposed.quantity
        max_risk = ctx.equity * self._max_pct
        if risk_total > max_risk:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"per-trade risk ${risk_total:.2f} > cap ${max_risk:.2f} "
                    f"({self._max_pct * 100:.2f}% of equity ${ctx.equity:.2f})"
                ),
                metadata={
                    "risk_total": str(risk_total),
                    "cap": str(max_risk),
                    "risk_per_unit": str(risk_per_unit),
                    "qty": str(proposed.quantity),
                },
            )
        return None


class MaxPositionSize(RiskLimit):
    """Cap notional position value as a % of equity."""

    name = "max_position_size"

    def __init__(self, max_pct: Decimal | float | str = "0.20") -> None:
        self._max_pct = Decimal(str(max_pct))
        if self._max_pct <= 0 or self._max_pct > Decimal("1"):
            raise ValueError("max_position_size must be in (0, 1]")

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        notional = bar.close * proposed.quantity
        cap = ctx.equity * self._max_pct
        # Add existing position notional so adding-on hits the cap too.
        existing = ctx.position(signal.instrument)
        existing_notional = (
            abs(existing.quantity) * bar.close if existing is not None else Decimal("0")
        )
        total = notional + existing_notional
        if total > cap:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"position notional ${total:.2f} > cap ${cap:.2f} "
                    f"({self._max_pct * 100:.2f}% of equity)"
                ),
                metadata={"total": str(total), "cap": str(cap)},
            )
        return None
