"""Smart Order Router.

Sits between :class:`risk.RiskEngine`'s allowed orders and the broker. Decisions:

  - **Spread guard**: if the bid/ask spread is wider than ``max_spread_bps``,
    downgrade a market order to a limit-at-mid (or skip, configurable).
  - **Depth guard**: if order quantity > ``max_pct_of_top`` × top-of-book size,
    slice via the configured slicer.
  - **Size threshold**: orders above ``slice_above_qty`` are sliced via the
    configured slicer regardless of depth.
  - **Pass-through** otherwise.

Market view (latest quote + depth) comes from :class:`MarketView` which the
caller updates as ticks arrive. In backtest the router can be wired with a
stub-only view; in live the data adapter populates it.

The router is broker-agnostic — it consumes :class:`execution.base.BrokerAdapter`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from core.logging import get_logger
from core.schemas import Instrument, Order, OrderSide, OrderType
from execution.base import BrokerAdapter
from execution.slicer import ChildOrder, IcebergSlicer, Slicer

log = get_logger(__name__)


@dataclass
class Quote:
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: Decimal | None = None
    ask_size: Decimal | None = None

    @property
    def mid(self) -> Decimal | None:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / Decimal("2")
        return self.bid or self.ask

    @property
    def spread_bps(self) -> Decimal | None:
        if self.bid is None or self.ask is None or self.mid is None or self.mid == 0:
            return None
        return (self.ask - self.bid) / self.mid * Decimal("10000")


class MarketView:
    """Latest quote / depth per instrument. Updated by the data feed."""

    def __init__(self) -> None:
        self._quotes: dict[str, Quote] = {}

    def update_quote(
        self,
        instrument: Instrument,
        *,
        bid: Decimal | None = None,
        ask: Decimal | None = None,
        bid_size: Decimal | None = None,
        ask_size: Decimal | None = None,
    ) -> None:
        self._quotes[instrument.key] = Quote(bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size)

    def get(self, instrument: Instrument) -> Quote | None:
        return self._quotes.get(instrument.key)


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    parent: Order
    children: tuple[Order, ...]
    reason: str
    transformed: bool = False  # True if we modified order type (e.g., market → limit)


class SmartOrderRouter:
    """Wraps a broker; decides how to send each order.

    Strategy summary (in order):
      1. If a slicer is configured and (size > threshold OR > depth limit), slice.
      2. If spread guard is enabled and spread too wide for a market order, either
         downgrade to limit-at-mid or skip (per ``on_wide_spread``).
      3. Otherwise pass through directly to the broker.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        *,
        market_view: MarketView | None = None,
        slicer: Slicer | None = None,
        slice_above_qty: Decimal | float | str = "1000",
        max_spread_bps: Decimal | float | str = "20",
        on_wide_spread: str = "limit_at_mid",  # "limit_at_mid" | "skip"
        max_pct_of_top: float = 0.5,
    ) -> None:
        self._broker = broker
        self._view = market_view or MarketView()
        self._slicer = slicer
        self._slice_threshold = Decimal(str(slice_above_qty))
        self._max_spread_bps = Decimal(str(max_spread_bps))
        if on_wide_spread not in ("limit_at_mid", "skip"):
            raise ValueError("on_wide_spread must be 'limit_at_mid' or 'skip'")
        self._on_wide = on_wide_spread
        if not 0 < max_pct_of_top <= 10:
            raise ValueError("max_pct_of_top out of range")
        self._max_pct_of_top = max_pct_of_top

    @property
    def market_view(self) -> MarketView:
        return self._view

    async def route(self, parent: Order, *, now: datetime | None = None) -> RoutingDecision:
        """Decide + execute. Returns the decision and the list of children
        actually submitted to the broker."""
        now = now or datetime.now(tz=timezone.utc)
        quote = self._view.get(parent.instrument)

        # 1. Spread guard (only for market-type orders).
        transformed_parent = parent
        if parent.order_type is OrderType.MARKET and quote is not None and quote.spread_bps is not None:
            if quote.spread_bps > self._max_spread_bps:
                if self._on_wide == "skip":
                    log.warning(
                        "router_skipped_wide_spread",
                        symbol=parent.instrument.symbol,
                        spread_bps=str(quote.spread_bps),
                        cap=str(self._max_spread_bps),
                    )
                    return RoutingDecision(parent=parent, children=(), reason="wide_spread_skipped")
                # Downgrade to limit-at-mid.
                mid = quote.mid
                if mid is not None:
                    transformed_parent = parent.model_copy(
                        update={"order_type": OrderType.LIMIT, "limit_price": mid}
                    )

        # 2. Slicing decision.
        too_big = transformed_parent.quantity > self._slice_threshold
        too_thick = self._exceeds_depth(transformed_parent, quote)
        if self._slicer is not None and (too_big or too_thick):
            plan = self._slicer.plan(transformed_parent, now=now)
            submitted = await self._submit_plan(plan)
            return RoutingDecision(
                parent=transformed_parent,
                children=tuple(submitted),
                reason=f"sliced[too_big={too_big},too_thick={too_thick}]",
                transformed=transformed_parent is not parent,
            )

        # 3. Pass-through.
        accepted = await self._broker.submit(transformed_parent)
        return RoutingDecision(
            parent=transformed_parent,
            children=(accepted,),
            reason="passthrough",
            transformed=transformed_parent is not parent,
        )

    async def _submit_plan(self, plan: list[ChildOrder]) -> list[Order]:
        out: list[Order] = []
        for child in plan:
            if child.release_at is not None:
                delay = (child.release_at - datetime.now(tz=timezone.utc)).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
            accepted = await self._broker.submit(child.order)
            out.append(accepted)
        return out

    def _exceeds_depth(self, order: Order, quote: Quote | None) -> bool:
        if quote is None:
            return False
        top = quote.ask_size if order.side is OrderSide.BUY else quote.bid_size
        if top is None:
            return False
        return order.quantity > Decimal(str(self._max_pct_of_top)) * top
