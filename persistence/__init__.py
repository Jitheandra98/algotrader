"""Persistence layer — Postgres + TimescaleDB.

Schema (see migrations/001_initial.sql):
  Hypertables (time-series, partitioned by time):
    - ticks
    - bars
    - fills

  Regular tables (relational):
    - instruments
    - orders
    - positions
    - strategy_config
    - signals          (audit log of every signal emitted)
    - risk_decisions   (audit log of every risk gate decision)
    - kpi_snapshots
"""

from persistence.db import Database

__all__ = ["Database"]
