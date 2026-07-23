"""Kill-switch limits.

Two flavors:

  - :class:`KillSwitch` — manual / external trip. Once tripped, blocks every
    new order until ``reset()`` is called. Used by the operator UI (M8) and the
    health-checker (heartbeat lost, broker disconnect, etc.).
  - :class:`ConsecutiveLossKill` — auto-trip after N consecutive losing trades.
    Resets after a manual operator action or after a configurable cooldown
    period.

Per the brief: 'No strategy can bypass risk' and 'Never write code that disables
the kill-switch.' The trip state is persistent — closing and reopening the
process should not reset it. In M6 we keep it in memory; M8 wires it to Redis +
the operator UI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from core.logging import get_logger
from core.schemas import Order, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection

log = get_logger(__name__)


class KillSwitch(RiskLimit):
    """Manual or programmatic full-stop. Trip with :meth:`trip()`, reset with
    :meth:`reset()`. Multiple reasons can be recorded; ``rejections`` lists them."""

    name = "kill_switch"

    def __init__(self) -> None:
        self._tripped: bool = False
        self._reasons: list[str] = []
        self._tripped_at: datetime | None = None

    def trip(self, reason: str) -> None:
        if not self._tripped:
            self._tripped_at = datetime.now()
        self._tripped = True
        self._reasons.append(reason)
        log.critical("kill_switch_tripped", reason=reason)

    def reset(self) -> None:
        self._tripped = False
        self._reasons.clear()
        self._tripped_at = None
        log.warning("kill_switch_reset")

    @property
    def is_tripped(self) -> bool:
        return self._tripped

    @property
    def reasons(self) -> list[str]:
        return list(self._reasons)

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        if not self._tripped:
            return None
        return RiskRejection(
            limit=self.name,
            reason=f"kill switch active: {'; '.join(self._reasons[-3:])}",
            metadata={"tripped_at": self._tripped_at.isoformat() if self._tripped_at else None},
        )


class ConsecutiveLossKill(RiskLimit):
    """Auto-trip after N consecutive losing trades. Optional cooldown that lets
    the switch self-reset after ``cooldown`` time has passed."""

    name = "consecutive_loss_kill"

    def __init__(
        self,
        max_consecutive: int = 5,
        cooldown: timedelta | None = None,
    ) -> None:
        if max_consecutive < 2:
            raise ValueError("max_consecutive must be >= 2")
        self._max = max_consecutive
        self._cooldown = cooldown
        self._tripped_at: datetime | None = None

    def _is_tripped(self, ctx: RiskContext) -> bool:
        if self._tripped_at is None:
            return False
        if self._cooldown is not None and ctx.now - self._tripped_at >= self._cooldown:
            self._tripped_at = None
            return False
        return True

    def update_on_fill(self, fill, ctx: RiskContext) -> None:  # noqa: ANN001
        # The portfolio's trade ledger drives this — every closed trade is one
        # data point. We just inspect recent_trade_pnls.
        recent = ctx.recent_trade_pnls(self._max)
        if len(recent) < self._max:
            return
        if all(p < 0 for p in recent[-self._max:]):
            self._tripped_at = ctx.now
            log.critical("consecutive_loss_kill_tripped", n=self._max)

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        # Lazy check: if cooldown elapsed, allow.
        if not self._is_tripped(ctx):
            # Also auto-trip on check if the condition is met (e.g., if updates
            # were missed). This guarantees correctness even when
            # update_on_fill isn't wired.
            recent = ctx.recent_trade_pnls(self._max)
            if len(recent) >= self._max and all(p < 0 for p in recent[-self._max:]):
                self._tripped_at = ctx.now
            else:
                return None
        return RiskRejection(
            limit=self.name,
            reason=f"{self._max} consecutive losses — kill switch active",
            metadata={"max": self._max, "cooldown": str(self._cooldown) if self._cooldown else None},
        )
