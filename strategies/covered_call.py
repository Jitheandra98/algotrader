"""Covered call (options income) — *deferred*.

This strategy requires:

  - Option chain data (strikes, expirations, IV, Greeks)
  - Option-aware execution and Greeks tracking
  - Position tracking that includes both stock and short calls together
  - Roll / buyback logic on expiration approach or take-profit thresholds

None of those are available yet. Option chains are flagged for M2 follow-up
(adapter-level data work), Greeks tracking is M6 (risk), and option-aware
execution is M7.

For M5 we ship a documented stub class — registered in the registry so configs
can reference it — but the constructor raises so anyone wiring this into a live
or paper run gets an explicit, early error rather than silent breakage.

When implementing later, the rough algorithm is:

  1. Ensure long underlying position ≥ 100 shares per contract sold.
  2. Pick a call with ``target_delta`` (default 0.30) and ``target_dte`` (e.g.
     30-45 days).
  3. Sell the call (1 contract per 100 shares of underlying).
  4. Manage:
     - Take profit at ``profit_target_pct`` (e.g., 50% of premium collected).
     - Roll on expiration approach (``min_dte_to_roll`` ~ 7 days) to the next cycle.
     - Buy back if assignment risk gets high (delta > ``roll_delta_threshold``).
"""

from __future__ import annotations

from typing import ClassVar

from core.schemas import Bar
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import register


class CoveredCallStrategy(Strategy):
    name: ClassVar[str] = "covered_call"
    version: ClassVar[str] = "0-stub"

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        raise NotImplementedError(
            "covered_call requires option-chain data (M2+), Greeks tracking (M6), "
            "and option-aware execution (M7). Stub registered so config can "
            "reference the name; instantiate only after those land."
        )

    def configure(self, ctx: StrategyContext) -> None:  # pragma: no cover
        raise NotImplementedError

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:  # pragma: no cover
        raise NotImplementedError


@register("covered_call")
def _make(config: StrategyConfig) -> Strategy:
    return CoveredCallStrategy(config)
