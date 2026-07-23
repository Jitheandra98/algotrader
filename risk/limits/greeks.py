"""Aggregate options Greeks bound.

Caps the absolute portfolio delta / gamma / vega exposure. For non-options
positions, delta == quantity, and gamma/vega == 0 (stock has linear payoff).
For options, the broker / data adapter supplies Greeks per contract.

The limit takes a :class:`GreeksReader` callback that returns
``(delta, gamma, vega, theta)`` per ``(instrument, qty, price)``. Tests inject a
stub; production wires this to the options data adapter (M7).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from core.schemas import AssetClass, Order, OrderSide, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


@dataclass(frozen=True, slots=True)
class Greeks:
    delta: Decimal = Decimal("0")
    gamma: Decimal = Decimal("0")
    vega: Decimal = Decimal("0")
    theta: Decimal = Decimal("0")


GreeksReader = Callable[..., Greeks]
"""Signature: ``reader(instrument, signed_qty, price) -> Greeks``.

For non-options the default reader returns ``Greeks(delta=signed_qty)`` and zeros."""


def default_reader(instrument, signed_qty: Decimal, price: Decimal) -> Greeks:  # noqa: ANN001, ARG001
    if instrument.asset_class is AssetClass.OPTION:
        # Without an option data adapter we conservatively return 0 — the limit
        # is effectively inactive for options until M7 wires real data.
        return Greeks()
    # Equities/ETFs/futures: delta = signed quantity, no curvature.
    return Greeks(delta=signed_qty)


class AggregateGreeksBound(RiskLimit):
    name = "aggregate_greeks_bound"

    def __init__(
        self,
        max_abs_delta: Decimal | float | str | None = None,
        max_abs_gamma: Decimal | float | str | None = None,
        max_abs_vega: Decimal | float | str | None = None,
        reader: GreeksReader = default_reader,
    ) -> None:
        self._max_delta = Decimal(str(max_abs_delta)) if max_abs_delta is not None else None
        self._max_gamma = Decimal(str(max_abs_gamma)) if max_abs_gamma is not None else None
        self._max_vega = Decimal(str(max_abs_vega)) if max_abs_vega is not None else None
        if all(x is None for x in (self._max_delta, self._max_gamma, self._max_vega)):
            raise ValueError("at least one of max_abs_{delta,gamma,vega} must be set")
        self._reader = reader

    def _portfolio_greeks(self, ctx: RiskContext) -> Greeks:
        total = Greeks()
        for pos in ctx.positions():
            price = pos.current_price or pos.average_price
            g = self._reader(pos.instrument, pos.quantity, price)
            total = Greeks(
                delta=total.delta + g.delta,
                gamma=total.gamma + g.gamma,
                vega=total.vega + g.vega,
                theta=total.theta + g.theta,
            )
        return total

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        sign = Decimal("1") if proposed.side is OrderSide.BUY else Decimal("-1")
        signed_qty = sign * proposed.quantity
        new_g = self._reader(signal.instrument, signed_qty, bar.close)
        agg = self._portfolio_greeks(ctx)
        agg = Greeks(
            delta=agg.delta + new_g.delta,
            gamma=agg.gamma + new_g.gamma,
            vega=agg.vega + new_g.vega,
            theta=agg.theta + new_g.theta,
        )
        if self._max_delta is not None and abs(agg.delta) > self._max_delta:
            return RiskRejection(
                limit=self.name,
                reason=f"|delta| {abs(agg.delta)} > cap {self._max_delta}",
                metadata={"delta": str(agg.delta)},
            )
        if self._max_gamma is not None and abs(agg.gamma) > self._max_gamma:
            return RiskRejection(
                limit=self.name,
                reason=f"|gamma| {abs(agg.gamma)} > cap {self._max_gamma}",
                metadata={"gamma": str(agg.gamma)},
            )
        if self._max_vega is not None and abs(agg.vega) > self._max_vega:
            return RiskRejection(
                limit=self.name,
                reason=f"|vega| {abs(agg.vega)} > cap {self._max_vega}",
                metadata={"vega": str(agg.vega)},
            )
        return None
