"""Simulated broker — order matching against bar OHLC with slippage + commission.

Match policy (no look-ahead — orders submitted during bar N's processing fill
against bar N+1's OHLC at earliest):

  - **Market**: fill at next bar's open + slippage.
  - **Limit BUY**: fill if next bar's low ≤ limit. Filled at the limit price
    (pessimistic — no price improvement modeled).
  - **Limit SELL**: fill if next bar's high ≥ limit. Filled at the limit.
  - **Stop BUY**: trigger if next bar's high ≥ stop. Fill at max(stop, open) + slip.
  - **Stop SELL**: trigger if next bar's low ≤ stop. Fill at min(stop, open) - slip.
  - **Stop-Limit**: stop trigger then limit behaviour (same bar attempt).
  - **MOC / LOC**: market / limit at the close of the bar in which they were placed.
  - **Bracket OCO**: parent + 2 child orders. On parent fill, children activate.
    When one child fills, the other auto-cancels.

The broker is single-threaded and deterministic — same inputs produce same fills.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from core.logging import get_logger
from core.schemas import (
    Bar,
    Fill,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from backtest.commission import CommissionModel, ZeroCommission
from backtest.slippage import SlippageModel, ZeroSlippage

log = get_logger(__name__)


class SimulatedBroker:
    """Match orders against forthcoming bars. Stateful (queue of pending orders)."""

    name = "sim"

    def __init__(
        self,
        *,
        slippage: SlippageModel | None = None,
        commission: CommissionModel | None = None,
    ) -> None:
        self._slippage = slippage or ZeroSlippage()
        self._commission = commission or ZeroCommission()
        self._pending: dict[UUID, Order] = {}
        # OCO bracket linkage: parent_id -> set(child_ids) and child_id -> sibling_id.
        self._oco_siblings: dict[UUID, UUID] = {}

    # ---- public API ----

    def submit(self, order: Order) -> Order:
        """Accept an order. Returns the order with status updated to NEW."""
        accepted = order.model_copy(update={"status": OrderStatus.NEW, "broker": self.name})
        self._pending[accepted.id] = accepted
        log.debug(
            "broker_order_accepted",
            order_id=str(accepted.id),
            symbol=accepted.instrument.symbol,
            side=accepted.side.value,
            type=accepted.order_type.value,
            qty=str(accepted.quantity),
        )
        return accepted

    def submit_bracket(
        self,
        parent: Order,
        *,
        take_profit_price: Decimal | None = None,
        stop_loss_price: Decimal | None = None,
    ) -> tuple[Order, Order | None, Order | None]:
        """Submit a parent + optional TP/SL pair. Children are *inactive* until the
        parent fills; once one child fills, the other is cancelled (OCO).
        """
        parent_accepted = self.submit(parent)

        opposite_side = OrderSide.SELL if parent.side is OrderSide.BUY else OrderSide.BUY

        tp_order: Order | None = None
        sl_order: Order | None = None
        if take_profit_price is not None:
            tp_order = Order(
                client_order_id=f"{parent.client_order_id}-tp",
                instrument=parent.instrument,
                side=opposite_side,
                order_type=OrderType.LIMIT,
                quantity=parent.quantity,
                limit_price=take_profit_price,
                time_in_force=TimeInForce.GTC,
                parent_order_id=parent_accepted.id,
                strategy_id=parent.strategy_id,
                broker=self.name,
                status=OrderStatus.PENDING_NEW,  # inactive
            )
            self._pending[tp_order.id] = tp_order
        if stop_loss_price is not None:
            sl_order = Order(
                client_order_id=f"{parent.client_order_id}-sl",
                instrument=parent.instrument,
                side=opposite_side,
                order_type=OrderType.STOP,
                quantity=parent.quantity,
                stop_price=stop_loss_price,
                time_in_force=TimeInForce.GTC,
                parent_order_id=parent_accepted.id,
                strategy_id=parent.strategy_id,
                broker=self.name,
                status=OrderStatus.PENDING_NEW,
            )
            self._pending[sl_order.id] = sl_order
        if tp_order is not None and sl_order is not None:
            self._oco_siblings[tp_order.id] = sl_order.id
            self._oco_siblings[sl_order.id] = tp_order.id
        return parent_accepted, tp_order, sl_order

    def cancel(self, order_id: UUID) -> bool:
        order = self._pending.get(order_id)
        if order is None:
            return False
        self._pending.pop(order_id)
        sibling = self._oco_siblings.pop(order_id, None)
        if sibling is not None:
            self._oco_siblings.pop(sibling, None)
        return True

    @property
    def pending_orders(self) -> list[Order]:
        return list(self._pending.values())

    def match(self, bar: Bar, atr: Decimal | None = None) -> list[Fill]:
        """Match all pending orders on this instrument against ``bar``. Returns
        the fills generated. The broker mutates its pending queue internally."""
        fills: list[Fill] = []
        # Snapshot order IDs because we mutate _pending while iterating.
        to_check = [
            oid
            for oid, o in self._pending.items()
            if o.instrument.key == bar.instrument.key
        ]
        for oid in to_check:
            order = self._pending.get(oid)
            if order is None:
                continue
            if order.status is OrderStatus.PENDING_NEW:
                # Bracket child waiting for parent to fill — skip.
                continue
            fill = self._match_order(order, bar, atr=atr)
            if fill is not None:
                fills.append(fill)
                self._on_filled(order)
        return fills

    # ---- matching internals ----

    def _match_order(self, order: Order, bar: Bar, *, atr: Decimal | None) -> Fill | None:
        match order.order_type:
            case OrderType.MARKET:
                price = bar.open
            case OrderType.MOC:
                price = bar.close
            case OrderType.LIMIT:
                price = self._match_limit(order, bar)
                if price is None:
                    return None
            case OrderType.LOC:
                # Limit-on-close: fill at close if condition met.
                if order.side is OrderSide.BUY and bar.close > order.limit_price:  # type: ignore[operator]
                    return None
                if order.side is OrderSide.SELL and bar.close < order.limit_price:  # type: ignore[operator]
                    return None
                price = order.limit_price  # type: ignore[assignment]
            case OrderType.STOP:
                price = self._match_stop(order, bar, atr=atr)
                if price is None:
                    return None
            case OrderType.STOP_LIMIT:
                triggered = self._match_stop(order, bar, atr=atr, return_trigger_only=True)
                if triggered is None:
                    return None
                # After trigger, behave as a limit using the same bar.
                price = self._match_limit(order, bar)
                if price is None:
                    return None
            case OrderType.TRAILING_STOP:
                # Trailing stops require state across bars; defer full impl to live
                # execution. In backtest treat them as plain stops.
                price = self._match_stop(order, bar, atr=atr)
                if price is None:
                    return None
            case _:
                return None
        assert price is not None
        # Apply slippage for market-type orders (open / close trigger).
        if order.order_type in (OrderType.MARKET, OrderType.STOP, OrderType.STOP_LIMIT, OrderType.TRAILING_STOP):
            slip = self._slippage.slip(
                instrument=order.instrument,
                side=order.side,
                reference_price=price,
                quantity=order.quantity,
                atr=atr,
            )
            price = price + slip
        # Build fill.
        fill = Fill(
            order_id=order.id,
            instrument=order.instrument,
            side=order.side,
            quantity=order.quantity,
            price=price,
            timestamp=bar.timestamp,
            commission=Decimal("0"),  # set after model so we can compute on the fill
        )
        comm = self._commission.commission(fill)
        if comm != 0:
            fill = fill.model_copy(update={"commission": comm})
        log.debug(
            "broker_fill",
            order_id=str(order.id),
            symbol=order.instrument.symbol,
            side=order.side.value,
            qty=str(order.quantity),
            price=str(price),
            commission=str(comm),
        )
        return fill

    def _match_limit(self, order: Order, bar: Bar) -> Decimal | None:
        assert order.limit_price is not None
        if order.side is OrderSide.BUY:
            # Filled if the bar trades down to (or below) the limit.
            if bar.low <= order.limit_price:
                return order.limit_price
            return None
        # SELL: filled if bar trades up to (or above) the limit.
        if bar.high >= order.limit_price:
            return order.limit_price
        return None

    def _match_stop(
        self,
        order: Order,
        bar: Bar,
        *,
        atr: Decimal | None,  # noqa: ARG002 (kept for parity; slip applied separately)
        return_trigger_only: bool = False,
    ) -> Decimal | None:
        assert order.stop_price is not None
        if order.side is OrderSide.BUY:
            # Triggered if bar trades up through the stop.
            if bar.high >= order.stop_price:
                if return_trigger_only:
                    return order.stop_price
                # Fill at max(stop, open) — we may have gapped above the stop.
                return max(order.stop_price, bar.open)
            return None
        # SELL
        if bar.low <= order.stop_price:
            if return_trigger_only:
                return order.stop_price
            return min(order.stop_price, bar.open)
        return None

    def _on_filled(self, order: Order) -> None:
        self._pending.pop(order.id, None)
        # OCO: cancel sibling.
        sibling = self._oco_siblings.pop(order.id, None)
        if sibling is not None:
            self._oco_siblings.pop(sibling, None)
            self._pending.pop(sibling, None)
        # Activate children if this is a parent.
        for child_id, child in list(self._pending.items()):
            if child.parent_order_id == order.id and child.status is OrderStatus.PENDING_NEW:
                self._pending[child_id] = child.model_copy(update={"status": OrderStatus.NEW})
