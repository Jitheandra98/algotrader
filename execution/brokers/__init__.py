"""Concrete broker adapters.

Heavy adapters are lazy-imported so processes that don't need them don't pay the
``httpx`` / ``websockets`` dependency cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from execution.brokers.alpaca_broker import AlpacaBroker

__all__ = ["AlpacaBroker"]


def __getattr__(name: str) -> Any:
    if name == "AlpacaBroker":
        from execution.brokers.alpaca_broker import AlpacaBroker as _AB
        return _AB
    raise AttributeError(f"module 'execution.brokers' has no attribute {name!r}")
