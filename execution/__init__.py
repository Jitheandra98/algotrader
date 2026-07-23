"""Execution layer — broker-agnostic order router and adapters (M7).

Public surface:
  - :class:`BrokerAdapter`            — ABC every live broker implements.
  - :class:`AlpacaBroker`             — Alpaca paper / live broker.
  - :class:`SmartOrderRouter`         — spread / depth / size-aware router.
  - :class:`MarketView`               — latest quotes per instrument.
  - Slicers: :class:`SimpleSplitSlicer`, :class:`TwapSlicer`, :class:`IcebergSlicer`.
  - :class:`FillQualityTracker`       — per-fill latency + slippage.
  - :class:`LiveEngine`               — async runtime that wires it all together.
"""

from typing import TYPE_CHECKING, Any

from execution.base import (
    AccountInfo,
    BrokerAdapter,
    ExecutionEvent,
    ExecutionReport,
)
from execution.live_engine import LiveContext, LiveEngine

if TYPE_CHECKING:
    from execution.brokers.alpaca_broker import AlpacaBroker
from execution.quality import FillQualityTracker, FillRecord, QualityStats, reference_price_for
from execution.router import MarketView, Quote, RoutingDecision, SmartOrderRouter
from execution.slicer import ChildOrder, IcebergSlicer, SimpleSplitSlicer, Slicer, TwapSlicer

def __getattr__(name: str) -> Any:
    """PEP 562 lazy-load for heavy adapters (httpx/websockets)."""
    if name == "AlpacaBroker":
        from execution.brokers.alpaca_broker import AlpacaBroker as _AB
        return _AB
    raise AttributeError(f"module 'execution' has no attribute {name!r}")


__all__ = [
    "AccountInfo",
    "AlpacaBroker",
    "BrokerAdapter",
    "ChildOrder",
    "ExecutionEvent",
    "ExecutionReport",
    "FillQualityTracker",
    "FillRecord",
    "IcebergSlicer",
    "LiveContext",
    "LiveEngine",
    "MarketView",
    "QualityStats",
    "Quote",
    "RoutingDecision",
    "SimpleSplitSlicer",
    "Slicer",
    "SmartOrderRouter",
    "TwapSlicer",
    "reference_price_for",
]
