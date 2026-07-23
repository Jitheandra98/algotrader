"""Per-strategy capital allocation cap."""

from __future__ import annotations

from decimal import Decimal

from core.schemas import Order, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


class PerStrategyAllocation(RiskLimit):
    """Cap notional exposure per strategy as a % of equity (or absolute $).

    For each known strategy_id, sums up notional value across positions whose
    last opener was that strategy. If adding the proposed order would exceed
    the cap, the trade is blocked.

    ``allocations`` is a dict ``{strategy_id: pct}`` where each value is the
    fraction of equity allowed for that strategy. Strategies not listed get
    ``default_pct`` (default 0.20 = 20% per strategy).

    Position-to-strategy attribution: the portfolio's ``strategy_id`` field on
    each position is the truth source. M5 strategies pass ``signal.strategy_id``
    through to the order; M6 portfolio is the canonical record.
    """

    name = "per_strategy_allocation"

    def __init__(
        self,
        allocations: dict[str, Decimal | float | str] | None = None,
        *,
        default_pct: Decimal | float | str = "0.20",
    ) -> None:
        self._alloc: dict[str, Decimal] = {
            k: Decimal(str(v)) for k, v in (allocations or {}).items()
        }
        self._default = Decimal(str(default_pct))
        if self._default <= 0 or self._default > Decimal("1"):
            raise ValueError("default_pct must be in (0, 1]")
        for k, v in self._alloc.items():
            if v < 0 or v > Decimal("1"):
                raise ValueError(f"allocation for {k} must be in [0, 1]")

    def _cap_for(self, strategy_id: str, equity: Decimal) -> Decimal:
        pct = self._alloc.get(strategy_id, self._default)
        return equity * pct

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return None
        strategy_id = signal.strategy_id
        # Sum notional of positions attributed to this strategy.
        used = Decimal("0")
        for pos in ctx.positions():
            if pos.strategy_id == strategy_id:
                price = pos.current_price or pos.average_price
                used += abs(pos.quantity) * price
        proposed_notional = bar.close * proposed.quantity
        total = used + proposed_notional
        cap = self._cap_for(strategy_id, ctx.equity)
        if total > cap:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"strategy '{strategy_id}' exposure ${total:.2f} > cap ${cap:.2f}"
                ),
                metadata={"total": str(total), "cap": str(cap), "strategy_id": strategy_id},
            )
        return None
