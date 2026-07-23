"""Strategy contract.

A strategy is a stateful component that consumes bars (and optionally ticks, fills,
and external events) and emits :class:`Signal` objects. Signals must pass through
the Risk layer (M6) before becoming orders — strategies do not place orders
directly. This guarantees risk rules cannot be bypassed.

The contract is intentionally narrow:

  - :meth:`configure` runs once at startup. Declare instruments, request indicators,
    set parameters from config.
  - :meth:`on_bar` runs on every bar. Read indicators from ``ctx``, emit signals
    via ``ctx.emit_signal()``.
  - :meth:`on_fill` (optional) reacts to broker fills (e.g., to log entry).
  - :meth:`on_event` (optional) reacts to non-market events (earnings, halts).

Strategies are single-instrument or multi-instrument — the contract supports both.
Engine wires one strategy instance per *configuration*, not per instrument.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Protocol

from core.schemas import (
    Bar,
    Fill,
    Instrument,
    Position,
    Signal,
    Tick,
)
from features.base import IndicatorReading


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Static configuration passed at construction. Strategies should be pure
    functions of (config, market state) — no mutable globals."""

    name: str
    instruments: Sequence[Instrument]
    parameters: dict[str, Any] = field(default_factory=dict)


class StrategyContext(Protocol):
    """The interface strategies use to talk to the engine. Implementations:
      - :class:`backtest.context.BacktestContext` (M4)
      - :class:`runtime.LiveContext` (M5/M7)
    """

    # Time
    @property
    def now(self) -> datetime: ...

    # Indicators — engine wires these up based on the strategy's configure() call.
    def indicator(self, key: str) -> IndicatorReading | None: ...

    # Portfolio state — read-only views.
    def position(self, instrument: Instrument) -> Position | None: ...

    @property
    def cash(self) -> Decimal: ...

    @property
    def equity(self) -> Decimal: ...

    # Outputs.
    def emit_signal(self, signal: Signal) -> None: ...

    # Indicator request — strategies call this from configure() to register their
    # indicators with the engine. The engine then updates them per bar and the
    # strategy retrieves them by key in on_bar().
    def request_indicator(
        self,
        instrument: Instrument,
        indicator_name: str,
        *,
        key: str | None = None,
        **params: Any,
    ) -> str: ...


class Strategy(ABC):
    """Base for all strategies. Subclasses set :attr:`name`, :attr:`version`, and
    implement :meth:`configure` + :meth:`on_bar`."""

    name: ClassVar[str] = "abstract"
    version: ClassVar[str] = "0"

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    @abstractmethod
    def configure(self, ctx: StrategyContext) -> None:
        """One-time setup. Request indicators, initialize state."""

    @abstractmethod
    def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        """Called for every bar on a subscribed instrument."""

    def on_tick(self, tick: Tick, ctx: StrategyContext) -> None:
        """Optional tick handler. Default no-op."""
        return None

    def on_fill(self, fill: Fill, ctx: StrategyContext) -> None:
        """Optional fill handler. Default no-op."""
        return None

    def on_event(self, event: dict[str, Any], ctx: StrategyContext) -> None:
        """Optional non-market event handler. Default no-op."""
        return None
