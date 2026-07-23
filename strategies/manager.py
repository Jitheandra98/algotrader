"""StrategyManager — Strategy-shaped wrapper around multiple child strategies.

The engine sees one Strategy; the manager fans bars out to its children and
collects emitted signals into the shared context. Children with lifecycle state
:attr:`Lifecycle.DISABLED` are skipped entirely (no on_bar, no on_fill).

Indicator deduplication: when two children both request, say, RSI(14) on SPY, the
context only instantiates *one* RSI and returns the same key. This is correct
behaviour — both children read the same reading. The dedup happens in
:class:`backtest.engine.BacktestContext`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.logging import get_logger
from core.schemas import Bar, Fill, Tick
from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.registry import Lifecycle

log = get_logger(__name__)


@dataclass(frozen=True)
class StrategySlot:
    """A strategy + its current lifecycle state."""

    strategy: Strategy
    lifecycle: Lifecycle = Lifecycle.PAPER


class StrategyManager(Strategy):
    """A Strategy that delegates to children. Implements the same interface so the
    backtest / live engine doesn't care that it's a composite."""

    name = "manager"
    version = "1"

    def __init__(
        self,
        config: StrategyConfig,
        children: Iterable[StrategySlot],
    ) -> None:
        slots = list(children)
        # Build a route table: per-instrument → list[strategy] subscribed.
        routes: dict[str, list[StrategySlot]] = {}
        union: list = []
        seen_inst: set[str] = set()
        for slot in slots:
            for inst in slot.strategy.config.instruments:
                routes.setdefault(inst.key, []).append(slot)
                if inst.key not in seen_inst:
                    union.append(inst)
                    seen_inst.add(inst.key)
        # If the caller didn't list instruments, set them to the union of
        # children's. Otherwise honour what they passed (e.g. for a manager
        # subscribed to a strict subset).
        effective_config = (
            config
            if config.instruments
            else StrategyConfig(name=config.name, instruments=tuple(union), parameters=config.parameters)
        )
        super().__init__(effective_config)
        self._slots: list[StrategySlot] = slots
        self._routes: dict[str, list[StrategySlot]] = routes

    @property
    def slots(self) -> list[StrategySlot]:
        return list(self._slots)

    def set_lifecycle(self, strategy_name: str, lifecycle: Lifecycle) -> bool:
        """Change a child's lifecycle. Returns True if found."""
        for i, slot in enumerate(self._slots):
            if slot.strategy.name == strategy_name or slot.strategy.config.name == strategy_name:
                self._slots[i] = StrategySlot(slot.strategy, lifecycle)
                # Rebuild routes (they reference the slot tuples).
                self._routes.clear()
                for s in self._slots:
                    for inst in s.strategy.config.instruments:
                        self._routes.setdefault(inst.key, []).append(s)
                log.info("strategy_lifecycle_changed", name=strategy_name, lifecycle=lifecycle.value)
                return True
        return False

    def configure(self, ctx: StrategyContext) -> None:
        for slot in self._slots:
            if slot.lifecycle is Lifecycle.DISABLED:
                continue
            slot.strategy.configure(ctx)
            log.info("strategy_configured", name=slot.strategy.name, lifecycle=slot.lifecycle.value)

    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        subscribers = self._routes.get(bar.instrument.key, [])
        for slot in subscribers:
            if slot.lifecycle is Lifecycle.DISABLED:
                continue
            slot.strategy.on_bar(bar, ctx)

    def on_tick(self, tick: Tick, ctx: StrategyContext) -> None:
        subscribers = self._routes.get(tick.instrument.key, [])
        for slot in subscribers:
            if slot.lifecycle is Lifecycle.DISABLED:
                continue
            slot.strategy.on_tick(tick, ctx)

    def on_fill(self, fill: Fill, ctx: StrategyContext) -> None:
        # Route to the strategy that placed the order, identified by the fill's
        # originating signal's strategy_id (carried via order.strategy_id).
        # Without explicit routing we fall back to dispatching to every subscriber
        # for that instrument; each child is responsible for filtering by id.
        subscribers = self._routes.get(fill.instrument.key, [])
        for slot in subscribers:
            if slot.lifecycle is Lifecycle.DISABLED:
                continue
            slot.strategy.on_fill(fill, ctx)
