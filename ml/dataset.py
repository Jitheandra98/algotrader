"""Dataset construction from feature store + trade outcomes.

Joins feature snapshots (one per signal) with the actual win/loss outcome of
the resulting trade. Strict no-leakage: a feature record's timestamp must be
*before* the trade exit time, and we only label records for which a trade
actually fired (or, when ``label_unfilled_as_no_trade``, label unfilled signals
as a third class — by default we drop them).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

import numpy as np

from ml.feature_store import FeatureRecord, FeatureStore


@dataclass(frozen=True, slots=True)
class Dataset:
    """Materialized matrix view of (features, labels, metadata)."""

    X: np.ndarray
    y: np.ndarray
    feature_names: list[str]
    timestamps: list[datetime]
    signal_ids: list[str]

    def __len__(self) -> int:
        return len(self.y)

    @property
    def n_features(self) -> int:
        return self.X.shape[1] if self.X.ndim == 2 else 0


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    """The minimal trade summary needed to label a feature record."""

    signal_id: str    # links to FeatureRecord.signal_id
    pnl: Decimal
    exit_time: datetime


class DatasetBuilder:
    """Build a :class:`Dataset` from a :class:`FeatureStore` and a list of
    :class:`TradeOutcome` objects.

    The label is ``1`` for a winning trade (``pnl > 0``) and ``0`` for a
    losing trade. Break-even trades (``pnl == 0``) are dropped by default —
    pass ``include_zero_pnl=True`` to keep them as losses.

    Feature columns are the **intersection** of feature names that appear in
    every kept record (default), or the **union** padded with ``fill_value``
    if ``align="union"``. ``intersection`` is safer; ``union`` is more
    permissive when features were added mid-run.
    """

    def __init__(
        self,
        *,
        store: FeatureStore,
        include_zero_pnl: bool = False,
        align: str = "intersection",
        fill_value: float = 0.0,
    ) -> None:
        if align not in ("intersection", "union"):
            raise ValueError("align must be 'intersection' or 'union'")
        self._store = store
        self._include_zero = include_zero_pnl
        self._align = align
        self._fill_value = fill_value

    def build(self, outcomes: Iterable[TradeOutcome]) -> Dataset:
        outcomes = list(outcomes)
        if not outcomes:
            return Dataset(np.empty((0, 0)), np.empty((0,)), [], [], [])

        # Join: for each outcome, find the matching feature record.
        rows: list[FeatureRecord] = []
        labels: list[int] = []
        for o in outcomes:
            from uuid import UUID
            try:
                sid = UUID(o.signal_id)
            except (ValueError, TypeError):
                continue
            r = self._store.get(sid)
            if r is None:
                continue
            if not self._include_zero and o.pnl == 0:
                continue
            # No-leakage check: features must be timestamped before exit.
            if r.timestamp >= o.exit_time:
                continue
            rows.append(r)
            labels.append(1 if o.pnl > 0 else 0)

        if not rows:
            return Dataset(np.empty((0, 0)), np.empty((0,)), [], [], [])

        # Column alignment.
        if self._align == "intersection":
            cols: set[str] | None = None
            for r in rows:
                keys = set(r.features.keys())
                cols = keys if cols is None else cols & keys
            feature_names = sorted(cols or set())
        else:  # union
            cols_u: set[str] = set()
            for r in rows:
                cols_u |= set(r.features.keys())
            feature_names = sorted(cols_u)

        if not feature_names:
            return Dataset(np.empty((0, 0)), np.empty((0,)), [], [], [])

        X = np.zeros((len(rows), len(feature_names)), dtype=np.float64)
        for i, r in enumerate(rows):
            for j, name in enumerate(feature_names):
                X[i, j] = float(r.features.get(name, self._fill_value))

        y = np.array(labels, dtype=np.int64)
        timestamps = [r.timestamp for r in rows]
        signal_ids = [str(r.signal_id) for r in rows]
        return Dataset(X=X, y=y, feature_names=feature_names, timestamps=timestamps, signal_ids=signal_ids)
