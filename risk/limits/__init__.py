"""Pluggable risk limits. Each implements :class:`risk.base.RiskLimit`.

Built-in limits:

  Sizing / exposure
    PerTradeRiskCap        — max % equity at risk per trade
    MaxPositionSize        — max % equity per position
    PerStrategyAllocation  — max % equity per strategy
    GrossLeverage          — sum |position values| / equity ceiling
    NetLeverage            — net position value / equity ceiling

  Drawdown / health
    DailyDrawdownLimit     — block on intraday DD breach
    WeeklyDrawdownLimit    — block on weekly DD breach
    ConsecutiveLossKill    — kill-switch after N losses
    KillSwitch             — manual / external trip

  Regulatory
    PatternDayTrader       — < $25K margin, max 3 day-trades / 5 days
    ShortSaleRestriction   — uptick gate when flagged

  Multi-instrument
    SectorExposureLimit    — % equity per sector
    CorrelationLimit       — block adding to high-correlation cluster

  Options
    AggregateGreeksBound   — portfolio delta / gamma / vega caps

  Tax
    WashSaleGuard          — 30-day rule flag (optional block)
"""

from risk.limits.correlation import CorrelationLimit
from risk.limits.drawdown import DailyDrawdownLimit, WeeklyDrawdownLimit
from risk.limits.greeks import AggregateGreeksBound, GreeksReader
from risk.limits.kill_switch import ConsecutiveLossKill, KillSwitch
from risk.limits.leverage import GrossLeverage, NetLeverage
from risk.limits.pdt import PatternDayTrader
from risk.limits.per_strategy import PerStrategyAllocation
from risk.limits.per_trade import MaxPositionSize, PerTradeRiskCap
from risk.limits.sector import SectorExposureLimit
from risk.limits.ssr import ShortSaleRestriction
from risk.limits.tax import LotMethod, LotSelector, TaxLot, WashSaleGuard

__all__ = [
    "AggregateGreeksBound",
    "ConsecutiveLossKill",
    "CorrelationLimit",
    "DailyDrawdownLimit",
    "GreeksReader",
    "GrossLeverage",
    "KillSwitch",
    "LotMethod",
    "LotSelector",
    "MaxPositionSize",
    "NetLeverage",
    "PatternDayTrader",
    "PerStrategyAllocation",
    "PerTradeRiskCap",
    "SectorExposureLimit",
    "ShortSaleRestriction",
    "TaxLot",
    "WashSaleGuard",
    "WeeklyDrawdownLimit",
]
