"""Indicator registry — name → factory.

Strategies and config files refer to indicators by string. The registry maps each
name to a callable producing a fresh indicator instance.

Conventional names:
  ``sma``, ``ema``, ``dema``, ``tema``, ``wma``, ``hma``, ``alma``, ``lsma``, ``vwma``,
  ``rsi``, ``macd``, ``stoch``, ``willr``, ``cci``, ``vortex``, ``trix``,
  ``bbands``, ``keltner``, ``donchian``, ``envelope``,
  ``vwap``, ``obv``, ``adline``, ``rvol``,
  ``atr``, ``adx``, ``supertrend``, ``psar``, ``chandelier``, ``volstop``,
  ``linreg``, ``tdseq``, ``strat``.

Patterns and relative-strength register under ``pattern.<name>`` and
``rs.<flavour>`` namespaces respectively.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from features.base import Indicator

_Factory = Callable[..., Indicator]
REGISTRY: dict[str, _Factory] = {}


def register(name: str) -> Callable[[_Factory], _Factory]:
    """Decorator to register an indicator factory under ``name``.

    Usage::

        @register("sma")
        def _make_sma(period: int = 20) -> Indicator: ...
    """

    def deco(factory: _Factory) -> _Factory:
        if name in REGISTRY:
            raise ValueError(f"indicator name already registered: {name}")
        REGISTRY[name] = factory
        return factory

    return deco


def create(name: str, /, **kwargs: Any) -> Indicator:
    """Look up a factory and call it. Raises KeyError if name is unknown."""
    if name not in REGISTRY:
        raise KeyError(f"unknown indicator: {name}. Known: {sorted(REGISTRY)}")
    return REGISTRY[name](**kwargs)


def names() -> list[str]:
    """Sorted list of all registered indicator names."""
    return sorted(REGISTRY)
