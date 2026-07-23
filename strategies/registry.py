"""Strategy registry + lifecycle states.

Strategies refer to each other (and to config files) by string name. The registry
maps a name to a factory that builds an instance from a :class:`StrategyConfig`.

Lifecycle states:
  - ``DISABLED``: strategy exists but doesn't process bars or emit signals.
  - ``PAPER``: processes bars and emits signals normally; the broker is
    responsible for honouring the paper-vs-live distinction.
  - ``LIVE``: same as PAPER for the strategy itself — the difference is enforced
    at the broker / execution boundary (see :mod:`core.config.is_live_strategy`).

Adding a strategy:

  @register("rsi_mean_reversion")
  def _make(config: StrategyConfig) -> Strategy:
      return RsiMeanReversionStrategy(config)
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum

from strategies.base import Strategy, StrategyConfig


class Lifecycle(str, Enum):
    DISABLED = "DISABLED"
    PAPER = "PAPER"
    LIVE = "LIVE"


_Factory = Callable[[StrategyConfig], Strategy]
REGISTRY: dict[str, _Factory] = {}


def register(name: str) -> Callable[[_Factory], _Factory]:
    """Decorator to register a strategy factory."""

    def deco(factory: _Factory) -> _Factory:
        if name in REGISTRY:
            raise ValueError(f"strategy already registered: {name}")
        REGISTRY[name] = factory
        return factory

    return deco


def create(name: str, config: StrategyConfig) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy: {name}. Known: {sorted(REGISTRY)}")
    return REGISTRY[name](config)


def names() -> list[str]:
    return sorted(REGISTRY)
