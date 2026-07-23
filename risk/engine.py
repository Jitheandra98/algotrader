"""RiskEngine — pipeline: signal → sizer → order draft → limits → decision.

The engine is the single point of truth between strategies and the broker. The
brief is non-negotiable on this:

  "Risk management must sit between strategies and order execution — no strategy
   can bypass it."

Pipeline:
  1. ``signal`` arrives from strategy.
  2. Sizer proposes a quantity. If 0 → no order.
  3. Build a draft :class:`Order`.
  4. Every configured limit's ``check()`` is called. Limits that reject add
     :class:`RiskRejection` records.
  5. If *any* limit rejected, ``allowed=False``. Otherwise the draft is the
     final order.
  6. Audit log every decision (including allows — for forensics).

State updates: limits with state (PDT, kill-switch, drawdown, wash sale) get
``update_on_fill`` called by the engine when fills arrive.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any
from uuid import uuid4

from core.logging import get_logger
from core.schemas import (
    Direction,
    Fill,
    Order,
    OrderSide,
    OrderType,
    Signal,
)
from risk.base import RiskContext, RiskDecision, RiskLimit, RiskRejection, Sizer
from risk.sizing import FixedFractionalSizer

log = get_logger(__name__)


class RiskEngine:
    """Stateful pipeline of sizer + limits."""

    def __init__(
        self,
        *,
        sizer: Sizer | None = None,
        limits: Sequence[RiskLimit] | None = None,
        broker_name: str = "sim",
        audit_log_capacity: int = 10_000,
    ) -> None:
        self._sizer: Sizer = sizer or FixedFractionalSizer()
        self._limits: list[RiskLimit] = list(limits or [])
        self._broker_name = broker_name
        self._audit: list[RiskDecision] = []
        self._cap = audit_log_capacity

    @property
    def limits(self) -> list[RiskLimit]:
        return list(self._limits)

    @property
    def audit_log(self) -> list[RiskDecision]:
        return list(self._audit)

    def decide(self, signal: Signal, ctx: RiskContext) -> RiskDecision:
        rejections: list[RiskRejection] = []
        sizing_meta: dict[str, Any] = {}

        # 1. Exit / HOLD signals get a special path: close current position
        # entirely (EXIT) or no-op (HOLD).
        if signal.direction is Direction.HOLD:
            return self._record(
                RiskDecision(signal=signal, allowed=False, order=None, sizing_meta={"reason": "HOLD"})
            )
        if signal.direction is Direction.EXIT:
            pos = ctx.position(signal.instrument)
            if pos is None or pos.quantity == 0:
                return self._record(
                    RiskDecision(signal=signal, allowed=False, order=None, sizing_meta={"reason": "no position to exit"})
                )
            side = OrderSide.SELL if pos.quantity > 0 else OrderSide.BUY
            order = Order(
                client_order_id=f"{signal.strategy_id}-{signal.id.hex[:8]}",
                instrument=signal.instrument,
                side=side,
                order_type=OrderType.MARKET,
                quantity=abs(pos.quantity),
                strategy_id=signal.strategy_id,
                broker=self._broker_name,
            )
            return self._record(RiskDecision(signal=signal, allowed=True, order=order, sizing_meta={"mode": "exit"}))

        # 2. Sizer
        qty, sizing_meta = self._sizer.size(signal, ctx)
        if qty <= 0:
            return self._record(
                RiskDecision(
                    signal=signal,
                    allowed=False,
                    order=None,
                    rejections=(
                        RiskRejection(
                            limit="sizer",
                            reason=sizing_meta.get("reason", "sizer returned zero quantity"),
                            metadata=sizing_meta,
                        ),
                    ),
                    sizing_meta=sizing_meta,
                )
            )

        # 3. Draft order
        side = OrderSide.BUY if signal.direction is Direction.LONG else OrderSide.SELL
        draft = Order(
            client_order_id=f"{signal.strategy_id}-{signal.id.hex[:8]}",
            instrument=signal.instrument,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            strategy_id=signal.strategy_id,
            broker=self._broker_name,
        )

        # 4. Run all limits, collecting rejections.
        for limit in self._limits:
            rej = limit.check(draft, signal, ctx)
            if rej is not None:
                rejections.append(rej)

        if rejections:
            decision = RiskDecision(
                signal=signal,
                allowed=False,
                order=None,
                rejections=tuple(rejections),
                sizing_meta=sizing_meta,
            )
            log.warning(
                "risk_rejection",
                signal_id=str(signal.id),
                strategy=signal.strategy_id,
                instrument=signal.instrument.symbol,
                rejections=[r.limit for r in rejections],
            )
            return self._record(decision)

        log.debug(
            "risk_allow",
            signal_id=str(signal.id),
            strategy=signal.strategy_id,
            qty=str(qty),
        )
        return self._record(
            RiskDecision(signal=signal, allowed=True, order=draft, sizing_meta=sizing_meta)
        )

    def on_fill(self, fill: Fill, ctx: RiskContext) -> None:
        """Notify limits of a fill so stateful ones can update."""
        for limit in self._limits:
            limit.update_on_fill(fill, ctx)

    def _record(self, decision: RiskDecision) -> RiskDecision:
        self._audit.append(decision)
        if len(self._audit) > self._cap:
            self._audit = self._audit[-self._cap:]
        return decision
