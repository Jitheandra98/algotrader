"""Correlation cluster limit.

When a candidate trade is highly correlated with positions already on, the bot
is effectively making a bigger bet on the same factor than the operator
intended. This limit blocks new positions that would push correlation-weighted
exposure past a cap.

Concretely: for the candidate instrument, compute the *correlation-weighted
exposure* as ``Σ |ρ(candidate, p_i)| × notional_i`` over existing positions.
If this exceeds ``max_cluster_pct × equity``, reject.

Correlations come from :meth:`RiskContext.correlation` (None when not enough
history). The backtest engine maintains a rolling-returns correlation matrix.
"""

from __future__ import annotations

from decimal import Decimal

from core.schemas import Order, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


class CorrelationLimit(RiskLimit):
    name = "correlation_limit"

    def __init__(
        self,
        max_cluster_pct: Decimal | float | str = "0.40",
        rho_threshold: float = 0.6,
    ) -> None:
        self._max_pct = Decimal(str(max_cluster_pct))
        if self._max_pct <= 0 or self._max_pct > 1:
            raise ValueError("max_cluster_pct must be in (0, 1]")
        if not -1 <= rho_threshold <= 1:
            raise ValueError("rho_threshold must be in [-1, 1]")
        self._rho_threshold = rho_threshold

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        weighted = Decimal("0")
        for pos in ctx.positions():
            if pos.instrument.key == signal.instrument.key:
                continue
            rho = ctx.correlation(signal.instrument, pos.instrument)
            if rho is None:
                continue
            if abs(rho) < self._rho_threshold:
                continue
            price = pos.current_price or pos.average_price
            weighted += Decimal(str(abs(rho))) * abs(pos.quantity) * price
        added = bar.close * proposed.quantity
        # Treat the new position as having ρ=1 with itself (full weight).
        cluster = weighted + added
        cap = ctx.equity * self._max_pct
        if cluster > cap:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"correlation cluster ${cluster:.2f} > cap ${cap:.2f} "
                    f"(threshold ρ={self._rho_threshold})"
                ),
                metadata={"cluster": str(cluster), "cap": str(cap)},
            )
        return None
