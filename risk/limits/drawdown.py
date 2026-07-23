"""Drawdown ceilings: block new entries when intraday or weekly DD breaches.

Critical guardrail per the brief: 'a hard kill-switch on consecutive losses' and
drawdown caps. These limits don't auto-flatten existing positions — that's an
operator decision routed through the kill-switch (M8 alerts will surface it).
They block *new* entries until the next session/week refresh.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.schemas import Order, Signal
from risk.base import RiskContext, RiskLimit, RiskRejection

UTC = timezone.utc


class DailyDrawdownLimit(RiskLimit):
    """Block when intraday DD from session-open equity exceeds ``max_pct``.

    Session boundary is at ``session_reset_utc`` (default 00:00 UTC). For US
    equities you'd typically pass ``time(13, 30)`` (9:30 ET pre-DST) or have the
    config supply session times per instrument class.
    """

    name = "daily_drawdown_limit"

    def __init__(
        self,
        max_pct: Decimal | float | str = "0.03",
        session_reset_hour_utc: int = 0,
    ) -> None:
        self._max = Decimal(str(max_pct))
        if self._max <= 0 or self._max >= 1:
            raise ValueError("max_pct must be in (0, 1)")
        if not 0 <= session_reset_hour_utc <= 23:
            raise ValueError("session_reset_hour_utc must be in [0,23]")
        self._reset_hour = session_reset_hour_utc

    def _session_start(self, now: datetime) -> datetime:
        today = datetime(now.year, now.month, now.day, self._reset_hour, tzinfo=UTC)
        return today if now >= today else today - timedelta(days=1)

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        session_start = self._session_start(ctx.now)
        open_equity = ctx.equity_at(session_start)
        if open_equity is None or open_equity <= 0:
            return None
        dd = (open_equity - ctx.equity) / open_equity
        if dd >= self._max:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"daily drawdown {dd * 100:.2f}% >= cap {self._max * 100:.2f}% "
                    f"(session-open ${open_equity:.2f} → now ${ctx.equity:.2f})"
                ),
                metadata={"dd_pct": str(dd), "cap_pct": str(self._max)},
            )
        return None


class WeeklyDrawdownLimit(RiskLimit):
    """Block when DD from Monday-open equity exceeds ``max_pct``."""

    name = "weekly_drawdown_limit"

    def __init__(self, max_pct: Decimal | float | str = "0.07") -> None:
        self._max = Decimal(str(max_pct))
        if self._max <= 0 or self._max >= 1:
            raise ValueError("max_pct must be in (0, 1)")

    def _week_start(self, now: datetime) -> datetime:
        days_since_monday = now.weekday()  # Mon=0
        return datetime(now.year, now.month, now.day, 0, tzinfo=UTC) - timedelta(days=days_since_monday)

    def check(self, proposed: Order, signal: Signal, ctx: RiskContext) -> RiskRejection | None:
        wstart = self._week_start(ctx.now)
        open_equity = ctx.equity_at(wstart)
        if open_equity is None or open_equity <= 0:
            return None
        dd = (open_equity - ctx.equity) / open_equity
        if dd >= self._max:
            return RiskRejection(
                limit=self.name,
                reason=(
                    f"weekly drawdown {dd * 100:.2f}% >= cap {self._max * 100:.2f}%"
                ),
                metadata={"dd_pct": str(dd), "cap_pct": str(self._max)},
            )
        return None
