"""Trade journal store.

Per-trade JSONL records with the full provenance chain:
  - which strategy emitted the signal
  - what the signal's rationale was
  - what the risk decision was (allowed + rejections audit)
  - what the indicator snapshot looked like at entry
  - the fill price / slippage / latency
  - the eventual P&L on close

Backed by an append-only JSONL file by default; M10 swaps for TimescaleDB.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import orjson

from core.logging import get_logger

log = get_logger(__name__)

UTC = timezone.utc


def _decimal_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"cannot serialize {type(o).__name__}")


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One trade. Frozen + slots so we can hash + compare in tests."""

    trade_id: str           # UUID of the closing trade record
    signal_id: str          # UUID of the signal that initiated the trade
    strategy_id: str
    instrument: str         # symbol|asset_class
    side: str               # LONG / SHORT
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal
    commission: Decimal
    rationale: str
    risk_decision: dict[str, Any] = field(default_factory=dict)
    indicator_snapshot: dict[str, float] = field(default_factory=dict)
    fill_quality: dict[str, Any] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "signal_id": self.signal_id,
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "side": self.side,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price),
            "quantity": str(self.quantity),
            "pnl": str(self.pnl),
            "commission": str(self.commission),
            "rationale": self.rationale,
            "risk_decision": self.risk_decision,
            "indicator_snapshot": self.indicator_snapshot,
            "fill_quality": self.fill_quality,
            "tags": self.tags,
        }

    @classmethod
    def from_jsonable(cls, data: dict[str, Any]) -> "JournalEntry":
        def _ts(s: str) -> datetime:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)

        return cls(
            trade_id=data["trade_id"],
            signal_id=data["signal_id"],
            strategy_id=data["strategy_id"],
            instrument=data["instrument"],
            side=data["side"],
            entry_time=_ts(data["entry_time"]),
            exit_time=_ts(data["exit_time"]),
            entry_price=Decimal(data["entry_price"]),
            exit_price=Decimal(data["exit_price"]),
            quantity=Decimal(data["quantity"]),
            pnl=Decimal(data["pnl"]),
            commission=Decimal(data["commission"]),
            rationale=data["rationale"],
            risk_decision=data.get("risk_decision", {}),
            indicator_snapshot=data.get("indicator_snapshot", {}),
            fill_quality=data.get("fill_quality", {}),
            tags=data.get("tags", {}),
        )


class TradeJournal:
    """Append-only journal. Per-trade :class:`JournalEntry` records.

    Backed by an in-memory list plus an optional JSONL file for persistence.
    The store is intentionally narrow — no queries beyond chronological order
    and per-strategy filtering. Anything richer goes through the M10 DB layer.
    """

    def __init__(self, *, jsonl_path: Path | str | None = None) -> None:
        self._entries: list[JournalEntry] = []
        self._jsonl: Path | None = Path(jsonl_path) if jsonl_path else None

    def __len__(self) -> int:
        return len(self._entries)

    def record(self, entry: JournalEntry) -> None:
        self._entries.append(entry)
        if self._jsonl is not None:
            self._jsonl.parent.mkdir(parents=True, exist_ok=True)
            with self._jsonl.open("ab") as f:
                f.write(orjson.dumps(entry.to_jsonable(), default=_decimal_default))
                f.write(b"\n")

    def all(self) -> list[JournalEntry]:
        return list(self._entries)

    def by_strategy(self, strategy_id: str) -> list[JournalEntry]:
        return [e for e in self._entries if e.strategy_id == strategy_id]

    def by_instrument(self, instrument: str) -> list[JournalEntry]:
        return [e for e in self._entries if e.instrument == instrument]

    def since(self, ts: datetime) -> list[JournalEntry]:
        return [e for e in self._entries if e.exit_time >= ts]

    def summary(self) -> dict[str, Any]:
        if not self._entries:
            return {"n": 0}
        wins = [e for e in self._entries if e.is_winner]
        losses = [e for e in self._entries if not e.is_winner]
        total = sum((e.pnl for e in self._entries), Decimal("0"))
        return {
            "n": len(self._entries),
            "wins": len(wins),
            "losses": len(losses),
            "total_pnl": str(total),
            "win_rate": len(wins) / len(self._entries),
        }

    @classmethod
    def load_jsonl(cls, path: Path | str) -> "TradeJournal":
        j = cls()
        p = Path(path)
        if not p.exists():
            return j
        with p.open("rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                j._entries.append(JournalEntry.from_jsonable(orjson.loads(line)))
        return j
