"""Bar-price source selectors used by every price-based indicator.

TradingView convention: most indicators accept ``source`` ∈
{``close``, ``open``, ``high``, ``low``, ``hl2``, ``hlc3``, ``ohlc4``}.
"""

from __future__ import annotations

from typing import Literal

from core.schemas import Bar

Source = Literal["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"]


def select(bar: Bar, source: Source) -> float:
    if source == "close":
        return float(bar.close)
    if source == "open":
        return float(bar.open)
    if source == "high":
        return float(bar.high)
    if source == "low":
        return float(bar.low)
    if source == "hl2":
        return (float(bar.high) + float(bar.low)) / 2.0
    if source == "hlc3":
        return (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
    if source == "ohlc4":
        return (float(bar.open) + float(bar.high) + float(bar.low) + float(bar.close)) / 4.0
    raise ValueError(f"unknown source: {source}")
