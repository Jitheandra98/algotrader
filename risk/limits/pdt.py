"""Pattern Day Trader (PDT) rule.

FINRA: a "pattern day trader" is anyone who executes 4+ **day trades** within
**5 business days** in a margin account with less than ``$25,000`` equity.
Once tagged PDT, the account is restricted and may face a margin call.

The rule applies *only* to margin accounts and *only* below the $25K equity
threshold. Cash accounts and accounts with ≥ $25K are exempt.

A **day trade** is opening and closing the same position on the same trading
day. We track:
  - open events per (instrument, day) — opening trades
  - close events that pair against an open same-day

We block a trade that **would be the 4th** day-trade in any 5-business-day
window. Detection: count distinct day-trade-days in the trailing 5 business
days; if the proposed trade would create a 4th, reject.

Implementation note: this is an approximation — the *actual* PDT rule cares about
specific round-trips not days. But day-counting is the conservative version and
matches what most retail brokers actually enforce.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal

from core.schemas import Fill, Order, OrderSide, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection


def _is_weekday(d: datetime) -> bool:
    return d.weekday() < 5


def _business_day(d: datetime) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=d.tzinfo)


class PatternDayTrader(RiskLimit):
    name = "pattern_day_trader"

    def __init__(
        self,
        threshold_account_value: Decimal | float | str = "25000",
        max_day_trades_in_5_days: int = 3,
    ) -> None:
        self._threshold = Decimal(str(threshold_account_value))
        self._max = max_day_trades_in_5_days
        if self._max < 1:
            raise ValueError("max_day_trades must be >= 1")
        # Day-trade days observed in the trailing 5 business days.
        self._day_trade_days: deque[datetime] = deque(maxlen=64)
        # Track today's open dates per instrument so we can recognize a close
        # that would constitute a day-trade.
        self._opens_today: dict[str, datetime] = {}

    def update_on_fill(self, fill: Fill, ctx: RiskContext) -> None:
        day = _business_day(fill.timestamp)
        # An OPEN is a new position; a CLOSE is hitting an existing position
        # the same day.
        key = fill.instrument.key
        pos = ctx.position(fill.instrument)

        if pos is not None and pos.quantity != 0:
            # Position now exists → this was an opening fill (after our apply).
            self._opens_today[key] = day
        else:
            # Position now flat. If we have a same-day open recorded, this is a
            # day-trade close.
            opened_on = self._opens_today.pop(key, None)
            if opened_on == day:
                if not self._day_trade_days or self._day_trade_days[-1] != day:
                    self._day_trade_days.append(day)

    def _is_pdt_eligible(self, ctx: RiskContext) -> bool:
        return ctx.is_margin_account and ctx.account_value < self._threshold

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        if not self._is_pdt_eligible(ctx):
            return None
        today = _business_day(ctx.now)
        window_start = today - timedelta(days=7)  # roomy; we filter by business days below
        recent_days = [d for d in self._day_trade_days if d > window_start and _is_weekday(d)]
        # Filter to last 5 business days.
        if recent_days:
            # Trim to last 5 business days from today, inclusive.
            cutoff = today
            n = 0
            kept = []
            for d in sorted(recent_days, reverse=True):
                while n < 5 and cutoff > d:
                    cutoff -= timedelta(days=1)
                    if _is_weekday(cutoff):
                        n += 1
                if n >= 5:
                    break
                kept.append(d)
            recent_days = kept

        existing = len(recent_days)
        # Would this trade close a same-day open?
        opened_on = self._opens_today.get(signal.instrument.key)
        would_close = opened_on == today and proposed.side is (
            OrderSide.SELL if ctx.position(signal.instrument) and ctx.position(signal.instrument).quantity > 0  # type: ignore[union-attr]
            else OrderSide.BUY
        )
        # Trade count if this fires.
        projected = existing + (1 if would_close and today not in recent_days else 0)
        if projected >= self._max + 1:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"PDT: would be day-trade #{projected} in last 5 business days "
                    f"(account ${ctx.account_value:.2f} < ${self._threshold:.0f} margin threshold)"
                ),
                metadata={"existing_day_trades": existing, "max": self._max},
            )
        return None
