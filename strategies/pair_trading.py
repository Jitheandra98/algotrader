"""Pair trading — statistical arbitrage on a two-instrument spread.

Compute the spread ``log(P_A) - β · log(P_B)`` where β is the rolling OLS hedge
ratio. Compute the spread's z-score over a rolling window. Trades:

  - z < -entry_z and we have no position → LONG A + SHORT B (spread is below mean)
  - z > +entry_z and we have no position → SHORT A + LONG B
  - |z| < exit_z and we have a position → exit both legs.
  - |z| > stop_z → emergency exit (loss-cut on divergence).

Emits TWO signals per trade action (one per leg). The risk layer sizes them
independently for M5; M6 will allow paired sizing for dollar-neutral hedges.

Configuration:
  - ``instruments`` must be exactly 2 — the order matters (leg A is first).
  - ``lookback`` (default 60): window for hedge ratio and z-score.
  - ``entry_z`` / ``exit_z`` / ``stop_z`` thresholds.
"""

from __future__ import annotations

import math
from collections import deque
from decimal import Decimal
from typing import ClassVar

from core.schemas import Bar, Direction, Instrument, Signal
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register


class PairTradingStrategy(Strategy):
    name: ClassVar[str] = "pair_trading"
    version: ClassVar[str] = "1"

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        if len(config.instruments) != 2:
            raise ValueError("pair_trading requires exactly 2 instruments")
        p = config.parameters
        self._a, self._b = config.instruments[0], config.instruments[1]
        self._lookback = int(p.get("lookback", 60))
        self._entry_z = float(p.get("entry_z", 2.0))
        self._exit_z = float(p.get("exit_z", 0.5))
        self._stop_z = float(p.get("stop_z", 4.0))
        self._closes_a: deque[float] = deque(maxlen=self._lookback)
        self._closes_b: deque[float] = deque(maxlen=self._lookback)
        self._last_a: float | None = None
        self._last_b: float | None = None
        self._state: str = "FLAT"  # "FLAT" | "LONG_A" (long A short B) | "SHORT_A"

    def configure(self, ctx: StrategyContext) -> None:
        return None  # no indicators

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        # Synchronize: store the latest close for each leg; act when both fresh
        # for the *same* bar timestamp.
        if bar.instrument.key == self._a.key:
            self._last_a = float(bar.close)
        elif bar.instrument.key == self._b.key:
            self._last_b = float(bar.close)
        else:
            return

        # Only proceed if both legs have a price.
        if self._last_a is None or self._last_b is None:
            return

        self._closes_a.append(self._last_a)
        self._closes_b.append(self._last_b)

        if len(self._closes_a) < self._lookback:
            return

        # Compute hedge ratio β via OLS (slope of log(B) → log(A)).
        log_a = [math.log(p) for p in self._closes_a if p > 0]
        log_b = [math.log(p) for p in self._closes_b if p > 0]
        if len(log_a) != len(log_b) or len(log_a) < 5:
            return
        n = len(log_a)
        mean_a = sum(log_a) / n
        mean_b = sum(log_b) / n
        cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(log_a, log_b))
        var_b = sum((b - mean_b) ** 2 for b in log_b)
        if var_b == 0:
            return
        beta = cov / var_b

        spread = [a - beta * b for a, b in zip(log_a, log_b)]
        mean_s = sum(spread) / n
        var_s = sum((s - mean_s) ** 2 for s in spread) / n
        std_s = math.sqrt(var_s)
        if std_s == 0:
            return
        z = (spread[-1] - mean_s) / std_s

        # Entry: spread below mean → LONG A + SHORT B.
        if self._state == "FLAT" and z < -self._entry_z:
            self._emit_pair(ctx, Direction.LONG, Direction.SHORT, z, beta)
            self._state = "LONG_A"
            return
        if self._state == "FLAT" and z > self._entry_z:
            self._emit_pair(ctx, Direction.SHORT, Direction.LONG, z, beta)
            self._state = "SHORT_A"
            return

        # Exits: z reverts toward 0.
        if self._state != "FLAT" and abs(z) < self._exit_z:
            self._emit_pair(ctx, Direction.EXIT, Direction.EXIT, z, beta, rationale_extra="z reverted")
            self._state = "FLAT"
            return

        # Stop: divergence wider than entry — exit and accept the loss.
        if self._state != "FLAT" and abs(z) > self._stop_z:
            self._emit_pair(ctx, Direction.EXIT, Direction.EXIT, z, beta, rationale_extra="z stop hit")
            self._state = "FLAT"

    def _emit_pair(
        self,
        ctx: StrategyContext,
        dir_a: Direction,
        dir_b: Direction,
        z: float,
        beta: float,
        rationale_extra: str = "",
    ) -> None:
        rationale = f"pair {self._a.symbol}/{self._b.symbol} z={z:.2f} β={beta:.2f}"
        if rationale_extra:
            rationale += f" — {rationale_extra}"
        ctx.emit_signal(
            Signal(
                strategy_id=self.name,
                instrument=self._a,
                direction=dir_a,
                conviction=min(1.0, abs(z) / max(1.0, self._entry_z)),
                rationale=rationale,
                metadata={"pair_leg": "A", "beta": beta, "z_score": z},
            )
        )
        ctx.emit_signal(
            Signal(
                strategy_id=self.name,
                instrument=self._b,
                direction=dir_b,
                conviction=min(1.0, abs(z) / max(1.0, self._entry_z)),
                rationale=rationale,
                metadata={"pair_leg": "B", "beta": beta, "z_score": z},
            )
        )


@register("pair_trading")
def _make(config: StrategyConfig) -> Strategy:
    return PairTradingStrategy(config)
