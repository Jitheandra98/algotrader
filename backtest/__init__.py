"""Backtest engine, simulated broker, KPIs, walk-forward, Monte Carlo, tear sheet.

Same indicators → strategy → risk → execution code path as live. The only
difference is that the data source is a :class:`FixtureAdapter` or a DB reader and
the broker is :class:`SimulatedBroker`.
"""

from backtest.broker import SimulatedBroker
from backtest.commission import (
    CommissionModel,
    PercentCommission,
    PerShareCommission,
    PerTradeCommission,
    ZeroCommission,
)
from backtest.engine import BacktestEngine, BacktestResult
from backtest.kpis import KPISummary, compute_kpis
from backtest.monte_carlo import monte_carlo_paths
from backtest.portfolio import Portfolio
from backtest.slippage import (
    AtrSlippage,
    BpsSlippage,
    PercentSlippage,
    SlippageModel,
    ZeroSlippage,
)
from backtest.tearsheet import render_tearsheet
from backtest.walk_forward import WalkForwardRunner, WalkForwardWindow

__all__ = [
    "AtrSlippage",
    "BacktestEngine",
    "BacktestResult",
    "BpsSlippage",
    "CommissionModel",
    "KPISummary",
    "PercentCommission",
    "PercentSlippage",
    "PerShareCommission",
    "PerTradeCommission",
    "Portfolio",
    "SimulatedBroker",
    "SlippageModel",
    "WalkForwardRunner",
    "WalkForwardWindow",
    "ZeroCommission",
    "ZeroSlippage",
    "compute_kpis",
    "monte_carlo_paths",
    "render_tearsheet",
]
