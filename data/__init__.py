"""Data ingestion layer.

Adapters pull raw data from brokers/exchanges and emit normalized core schemas
(``Tick``, ``Bar``). The bus carries them to downstream layers (features, persistence).

Public entry points:
  - :class:`data.base.DataAdapter` — the contract every adapter implements
  - :class:`data.adapters.alpaca.AlpacaAdapter` — Alpaca paper/live (M2)
  - :class:`data.adapters.fixture.FixtureAdapter` — replay recorded data
  - :class:`data.aggregators.base.BarAggregator` — tick/bar → bar transforms
"""

from data.base import DataAdapter, FeedMode, HistoricalBarRequest

__all__ = ["DataAdapter", "FeedMode", "HistoricalBarRequest"]
