"""Position sizing methods.

Three are shipped:

  - :class:`FixedFractionalSizer` — risk a fixed % of equity per trade. Uses the
    signal's ``stop_price`` for stop distance; if no stop, falls back to a
    configurable target allocation %.
  - :class:`VolTargetedSizer` — risk a fixed % of equity but use ``k × ATR`` as
    the stop distance regardless of the signal's stop. ATR comes from
    :meth:`RiskContext.atr`.
  - :class:`KellySizer` — fraction-of-Kelly sizing using the strategy's recent
    win rate and payoff ratio. Defaults to **half-Kelly** (safer than full).

All sizers return positive quantities. Sign (BUY vs SELL) is determined by the
risk engine based on :attr:`Signal.direction` and current position.
"""

from __future__ import annotations

import statistics
from decimal import Decimal
from typing import Any

from core.schemas import Direction, Signal
from risk.base import RiskContext, Sizer


def _abs(x: Decimal) -> Decimal:
    return x if x >= 0 else -x


def _safe_div(num: Decimal, denom: Decimal) -> Decimal:
    return num / denom if denom != 0 else Decimal("0")


def _round_qty(q: Decimal, precision: str = "0.0001") -> Decimal:
    return q.quantize(Decimal(precision)) if q > 0 else Decimal("0")


class FixedFractionalSizer(Sizer):
    """Risk ``risk_per_trade_pct`` of equity per trade.

    If the signal has a ``stop_price``, quantity = (risk_amount) / |entry - stop|.
    Otherwise quantity = (target_alloc_pct × equity) / current_price.

    Defaults are conservative — 0.5% risk per trade (the brief's default), 10%
    target allocation when no stop is present.
    """

    name = "fixed_fractional"

    def __init__(
        self,
        risk_per_trade_pct: Decimal | float | str = "0.005",
        target_alloc_pct: Decimal | float | str = "0.10",
    ) -> None:
        self._risk_pct = Decimal(str(risk_per_trade_pct))
        self._alloc_pct = Decimal(str(target_alloc_pct))
        if self._risk_pct < 0 or self._risk_pct > Decimal("0.1"):
            raise ValueError("risk_per_trade_pct must be in [0, 0.10]")
        if self._alloc_pct < 0 or self._alloc_pct > 1:
            raise ValueError("target_alloc_pct must be in [0, 1]")

    def size(self, signal: Signal, ctx: RiskContext) -> tuple[Decimal, dict[str, Any]]:
        if signal.direction in (Direction.EXIT, Direction.HOLD):
            return Decimal("0"), {"method": self.name, "reason": "no sizing needed for exit/hold"}
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return Decimal("0"), {"method": self.name, "reason": "no price available"}
        price = bar.close
        equity = ctx.equity
        risk_amount = equity * self._risk_pct

        meta: dict[str, Any] = {
            "method": self.name,
            "risk_pct": str(self._risk_pct),
            "risk_amount": str(risk_amount),
            "price": str(price),
            "equity": str(equity),
        }

        if signal.stop_price is not None and signal.stop_price != price:
            risk_per_unit = _abs(price - signal.stop_price)
            qty = _safe_div(risk_amount, risk_per_unit)
            meta["risk_per_unit"] = str(risk_per_unit)
            meta["mode"] = "stop_based"
        else:
            alloc = equity * self._alloc_pct
            qty = _safe_div(alloc, price)
            meta["alloc_pct"] = str(self._alloc_pct)
            meta["mode"] = "alloc_based"

        # Conviction scales sizing 0..1.
        qty = qty * Decimal(str(signal.conviction))
        meta["conviction"] = str(signal.conviction)
        return _round_qty(qty), meta


class VolTargetedSizer(Sizer):
    """Quantity such that ``k × ATR × qty == risk_amount``.

    Useful when the strategy doesn't supply a stop or you prefer volatility-driven
    position sizing across instruments with different price levels.
    """

    name = "vol_targeted"

    def __init__(
        self,
        risk_per_trade_pct: Decimal | float | str = "0.005",
        atr_stop_multiple: Decimal | float | str = "2.0",
        fallback_alloc_pct: Decimal | float | str = "0.10",
    ) -> None:
        self._risk_pct = Decimal(str(risk_per_trade_pct))
        self._k = Decimal(str(atr_stop_multiple))
        self._fallback = Decimal(str(fallback_alloc_pct))
        if self._risk_pct < 0 or self._risk_pct > Decimal("0.1"):
            raise ValueError("risk_per_trade_pct out of range")
        if self._k <= 0:
            raise ValueError("atr_stop_multiple must be positive")

    def size(self, signal: Signal, ctx: RiskContext) -> tuple[Decimal, dict[str, Any]]:
        if signal.direction in (Direction.EXIT, Direction.HOLD):
            return Decimal("0"), {"method": self.name}
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return Decimal("0"), {"method": self.name, "reason": "no price"}
        atr = ctx.atr(signal.instrument)
        equity = ctx.equity
        risk_amount = equity * self._risk_pct

        meta: dict[str, Any] = {
            "method": self.name,
            "risk_pct": str(self._risk_pct),
            "risk_amount": str(risk_amount),
            "equity": str(equity),
        }

        if atr is not None and atr > 0:
            risk_per_unit = atr * self._k
            qty = _safe_div(risk_amount, risk_per_unit)
            meta["atr"] = str(atr)
            meta["k"] = str(self._k)
            meta["mode"] = "atr_based"
        else:
            alloc = equity * self._fallback
            qty = _safe_div(alloc, bar.close)
            meta["mode"] = "alloc_fallback"
            meta["fallback_pct"] = str(self._fallback)

        qty = qty * Decimal(str(signal.conviction))
        return _round_qty(qty), meta


class KellySizer(Sizer):
    """Kelly fraction sizing with conservative defaults.

    Kelly fraction for a binary edge:  f* = win_rate - (1 - win_rate) / payoff_ratio

    where ``payoff_ratio = avg_win / |avg_loss|``. Pure Kelly is rarely used in
    practice — too aggressive. Default is **half-Kelly** (``kelly_fraction=0.5``).

    Stats are computed from the strategy's recent closed trades available via
    :meth:`RiskContext.recent_trade_pnls`. Until enough trade history exists,
    falls back to a fixed % allocation.
    """

    name = "kelly"

    def __init__(
        self,
        kelly_fraction: float = 0.5,
        min_trades: int = 30,
        max_pct: Decimal | float | str = "0.25",
        fallback_alloc_pct: Decimal | float | str = "0.05",
    ) -> None:
        if not 0 < kelly_fraction <= 1.0:
            raise ValueError("kelly_fraction must be in (0, 1]")
        self._kf = kelly_fraction
        self._min_trades = min_trades
        self._max_pct = Decimal(str(max_pct))
        self._fallback = Decimal(str(fallback_alloc_pct))

    def size(self, signal: Signal, ctx: RiskContext) -> tuple[Decimal, dict[str, Any]]:
        if signal.direction in (Direction.EXIT, Direction.HOLD):
            return Decimal("0"), {"method": self.name}
        bar = ctx.last_bar(signal.instrument)
        if bar is None:
            return Decimal("0"), {"method": self.name, "reason": "no price"}
        price = bar.close
        equity = ctx.equity
        meta: dict[str, Any] = {"method": self.name, "equity": str(equity)}

        trades = ctx.recent_trade_pnls(self._min_trades * 2)
        if len(trades) < self._min_trades:
            alloc = equity * self._fallback
            qty = _safe_div(alloc, price)
            meta["mode"] = "fallback_insufficient_history"
            meta["trades"] = len(trades)
            return _round_qty(qty * Decimal(str(signal.conviction))), meta

        wins = [float(t) for t in trades if t > 0]
        losses = [abs(float(t)) for t in trades if t < 0]
        if not wins or not losses:
            alloc = equity * self._fallback
            qty = _safe_div(alloc, price)
            meta["mode"] = "fallback_no_two_sided_history"
            return _round_qty(qty * Decimal(str(signal.conviction))), meta

        win_rate = len(wins) / len(trades)
        avg_win = statistics.fmean(wins)
        avg_loss = statistics.fmean(losses)
        payoff = avg_win / avg_loss if avg_loss > 0 else 1.0
        # Kelly fraction (binary edge approximation).
        f_kelly = win_rate - (1.0 - win_rate) / payoff if payoff > 0 else 0.0
        f_kelly = max(0.0, f_kelly) * self._kf
        f_kelly = min(f_kelly, float(self._max_pct))
        meta["win_rate"] = win_rate
        meta["payoff"] = payoff
        meta["f_star"] = f_kelly

        alloc = equity * Decimal(str(f_kelly))
        qty = _safe_div(alloc, price)
        qty = qty * Decimal(str(signal.conviction))
        return _round_qty(qty), meta
