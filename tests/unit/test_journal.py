"""Trade journal tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from journal import JournalEntry, TradeJournal

UTC = timezone.utc


def _entry(*, pnl: str = "5", strategy: str = "sma") -> JournalEntry:
    ts = datetime(2026, 5, 25, tzinfo=UTC)
    return JournalEntry(
        trade_id=str(uuid4()),
        signal_id=str(uuid4()),
        strategy_id=strategy,
        instrument="SPY|ETF",
        side="LONG",
        entry_time=ts,
        exit_time=ts + timedelta(hours=1),
        entry_price=Decimal("100"),
        exit_price=Decimal("105"),
        quantity=Decimal("10"),
        pnl=Decimal(pnl),
        commission=Decimal("0.05"),
        rationale="test entry",
        risk_decision={"allowed": True, "rejections": []},
        indicator_snapshot={"rsi": 28.5, "atr": 1.2},
        fill_quality={"latency_ms": 80.0, "slippage_per_unit": "0.01"},
    )


def test_record_and_summary() -> None:
    j = TradeJournal()
    j.record(_entry(pnl="10"))
    j.record(_entry(pnl="-5"))
    j.record(_entry(pnl="20"))
    s = j.summary()
    assert s["n"] == 3
    assert s["wins"] == 2
    assert s["losses"] == 1
    assert Decimal(s["total_pnl"]) == Decimal("25")
    assert 0.65 < s["win_rate"] < 0.7


def test_by_strategy_filter() -> None:
    j = TradeJournal()
    j.record(_entry(strategy="sma"))
    j.record(_entry(strategy="rsi_mr"))
    j.record(_entry(strategy="sma"))
    assert len(j.by_strategy("sma")) == 2
    assert len(j.by_strategy("rsi_mr")) == 1


def test_jsonl_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    j = TradeJournal(jsonl_path=path)
    e = _entry(pnl="3.50")
    j.record(e)
    assert path.exists()

    loaded = TradeJournal.load_jsonl(path)
    assert len(loaded) == 1
    out = loaded.all()[0]
    assert out.trade_id == e.trade_id
    assert out.pnl == Decimal("3.50")
    assert out.indicator_snapshot["rsi"] == 28.5


def test_jsonable_decimal_preserved() -> None:
    e = _entry(pnl="123.456789")
    payload = e.to_jsonable()
    assert payload["pnl"] == "123.456789"  # round-tripped as string for precision
    parsed = JournalEntry.from_jsonable(payload)
    assert parsed.pnl == Decimal("123.456789")


def test_since_filter() -> None:
    j = TradeJournal()
    ts = datetime(2026, 5, 25, tzinfo=UTC)
    j.record(_entry())
    later = _entry()
    j.record(later)
    res = j.since(ts + timedelta(minutes=30))
    assert len(res) >= 1
