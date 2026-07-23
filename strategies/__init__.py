"""Strategies — plugin modules implementing :class:`Strategy`.

Importing this package registers every shipped strategy. Configs and the manager
look strategies up by name via :func:`create`.
"""

from strategies.base import Strategy, StrategyConfig, StrategyContext
from strategies.ensemble import (
    ConfirmationFilter,
    MinConvictionFilter,
    SignalFilter,
    StopOpposingSignalsFilter,
)
from strategies.manager import StrategyManager, StrategySlot
from strategies.registry import Lifecycle, REGISTRY, create, names, register

# Side-effect imports that register strategies with REGISTRY:
from strategies import (  # noqa: F401, E402
    bollinger_squeeze,
    covered_call,
    fvg_fill,
    pair_trading,
    rsi_mean_reversion,
    sma_crossover,
    td_sequential_reversal,
    vcp_breakout,
)

from strategies.sma_crossover import SmaCrossoverStrategy

__all__ = [
    "ConfirmationFilter",
    "Lifecycle",
    "MinConvictionFilter",
    "REGISTRY",
    "SignalFilter",
    "SmaCrossoverStrategy",
    "StopOpposingSignalsFilter",
    "Strategy",
    "StrategyConfig",
    "StrategyContext",
    "StrategyManager",
    "StrategySlot",
    "create",
    "names",
    "register",
]
