"""Concrete data adapters.

Heavy adapters (Alpaca, IBKR, CCXT) are lazily imported so test runs and unrelated
processes don't pay the dependency cost (httpx / websockets / etc.) to import the
package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from data.adapters.fixture import FixtureAdapter

if TYPE_CHECKING:
    from data.adapters.alpaca import AlpacaAdapter

__all__ = ["AlpacaAdapter", "FixtureAdapter"]


def __getattr__(name: str) -> Any:
    # PEP 562 module __getattr__ — defer until someone actually asks for AlpacaAdapter.
    if name == "AlpacaAdapter":
        from data.adapters.alpaca import AlpacaAdapter as _AlpacaAdapter

        return _AlpacaAdapter
    raise AttributeError(f"module 'data.adapters' has no attribute {name!r}")
