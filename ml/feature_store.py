"""Feature store — persist the indicator snapshot + signal context for every
signal emitted, so we can later train models that predict whether that signal
would have been a win or a loss.

Storage is intentionally simple in M9: in-memory + optional JSONL file writer.
Production setups will swap in TimescaleDB-backed storage in M10. The interface
is the same.

Each record key is the signal's UUID — guaranteed-unique within a run, and
correlates one-to-one with the eventual trade outcome (in :class:`journal.TradeJournal`).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import orjson

from core.logging import get_logger

log = get_logger(__name__)

UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class FeatureRecord:
    """One signal's feature snapshot."""

    signal_id: UUID
    strategy_id: str
    instrument_key: str
    timestamp: datetime
    direction: str                       # LONG / SHORT
    features: dict[str, float]           # indicator name → numeric value
    context: dict[str, Any] = field(default_factory=dict)  # tags, prices, etc.

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "signal_id": str(self.signal_id),
            "strategy_id": self.strategy_id,
            "instrument_key": self.instrument_key,
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction,
            "features": self.features,
            "context": self.context,
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> "FeatureRecord":
        ts = data["timestamp"]
        if isinstance(ts, str):
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            ts = datetime.fromisoformat(ts)
        return cls(
            signal_id=UUID(data["signal_id"]),
            strategy_id=data["strategy_id"],
            instrument_key=data["instrument_key"],
            timestamp=ts,
            direction=data["direction"],
            features=data["features"],
            context=data.get("context", {}),
        )


class FeatureStore:
    """In-memory store with optional JSONL append-on-write."""

    def __init__(self, *, jsonl_path: Path | str | None = None) -> None:
        self._records: dict[UUID, FeatureRecord] = {}
        self._jsonl: Path | None = Path(jsonl_path) if jsonl_path else None

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, signal_id: UUID | str) -> bool:
        return UUID(str(signal_id)) in self._records

    def add(self, record: FeatureRecord) -> None:
        self._records[record.signal_id] = record
        if self._jsonl is not None:
            self._jsonl.parent.mkdir(parents=True, exist_ok=True)
            with self._jsonl.open("ab") as f:
                f.write(orjson.dumps(record.to_jsonable()))
                f.write(b"\n")

    def add_many(self, records: Iterable[FeatureRecord]) -> int:
        n = 0
        for r in records:
            self.add(r)
            n += 1
        return n

    def get(self, signal_id: UUID) -> FeatureRecord | None:
        return self._records.get(signal_id)

    def records(self) -> list[FeatureRecord]:
        return list(self._records.values())

    def query(
        self,
        *,
        strategy_id: str | None = None,
        instrument_key: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[FeatureRecord]:
        out: list[FeatureRecord] = []
        for r in self._records.values():
            if strategy_id is not None and r.strategy_id != strategy_id:
                continue
            if instrument_key is not None and r.instrument_key != instrument_key:
                continue
            if since is not None and r.timestamp < since:
                continue
            if until is not None and r.timestamp >= until:
                continue
            out.append(r)
        return sorted(out, key=lambda r: r.timestamp)

    @classmethod
    def load_jsonl(cls, path: Path | str) -> "FeatureStore":
        store = cls()
        p = Path(path)
        if not p.exists():
            return store
        with p.open("rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                store._records[UUID(orjson.loads(line)["signal_id"])] = FeatureRecord.from_jsonable(orjson.loads(line))
        return store

    def clear(self) -> None:
        self._records.clear()
