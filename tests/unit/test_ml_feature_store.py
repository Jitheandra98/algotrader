"""Feature store round-trip + query tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from ml import FeatureRecord, FeatureStore

UTC = timezone.utc


def _record(strategy="s1", sym="SPY|ETF", offset_min=0, features=None) -> FeatureRecord:
    return FeatureRecord(
        signal_id=uuid4(),
        strategy_id=strategy,
        instrument_key=sym,
        timestamp=datetime(2026, 5, 25, 13, 30, tzinfo=UTC) + timedelta(minutes=offset_min),
        direction="LONG",
        features=features or {"rsi": 28.0, "atr": 1.5},
    )


def test_add_and_get_round_trip() -> None:
    store = FeatureStore()
    r = _record()
    store.add(r)
    got = store.get(r.signal_id)
    assert got == r


def test_query_filters_by_strategy_and_time() -> None:
    store = FeatureStore()
    a1 = _record(strategy="a", offset_min=0)
    a2 = _record(strategy="a", offset_min=30)
    b1 = _record(strategy="b", offset_min=10)
    store.add_many([a1, a2, b1])

    res = store.query(strategy_id="a")
    assert {r.signal_id for r in res} == {a1.signal_id, a2.signal_id}

    # since=13:35 keeps a2 (14:00) and b1 (13:40); drops a1 (13:30).
    res = store.query(since=datetime(2026, 5, 25, 13, 35, tzinfo=UTC))
    assert {r.signal_id for r in res} == {a2.signal_id, b1.signal_id}


def test_jsonl_persistence_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "features.jsonl"
    store = FeatureStore(jsonl_path=path)
    rs = [_record(offset_min=i) for i in range(3)]
    store.add_many(rs)
    assert path.exists()

    loaded = FeatureStore.load_jsonl(path)
    assert len(loaded) == 3
    for r in rs:
        assert loaded.get(r.signal_id) is not None


def test_jsonl_handles_missing_file(tmp_path: Path) -> None:
    loaded = FeatureStore.load_jsonl(tmp_path / "does_not_exist.jsonl")
    assert len(loaded) == 0


def test_contains() -> None:
    store = FeatureStore()
    r = _record()
    assert r.signal_id not in store
    store.add(r)
    assert r.signal_id in store
