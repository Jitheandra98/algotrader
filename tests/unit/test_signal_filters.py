"""Signal filter (ensemble) tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.schemas import AssetClass, Direction, Instrument, Signal
from strategies import (
    ConfirmationFilter,
    MinConvictionFilter,
    StopOpposingSignalsFilter,
)

UTC = timezone.utc


def _sig(strategy_id: str, direction: Direction, conviction: float = 0.5) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        instrument=Instrument(symbol="SPY", asset_class=AssetClass.ETF),
        direction=direction,
        conviction=conviction,
        rationale="t",
    )


def test_min_conviction_filter() -> None:
    f = MinConvictionFilter(threshold=0.7)
    now = datetime(2026, 5, 25, tzinfo=UTC)
    signals = [_sig("a", Direction.LONG, 0.5), _sig("b", Direction.LONG, 0.8)]
    out = f.filter(signals, now=now)
    assert len(out) == 1
    assert out[0].strategy_id == "b"


def test_confirmation_filter_requires_min_votes() -> None:
    f = ConfirmationFilter(min_votes=2, window=timedelta(minutes=5))
    now = datetime(2026, 5, 25, tzinfo=UTC)
    out1 = f.filter([_sig("a", Direction.LONG)], now=now)
    assert out1 == []  # only 1 vote, threshold is 2
    out2 = f.filter([_sig("b", Direction.LONG)], now=now + timedelta(minutes=1))
    assert len(out2) == 1
    assert out2[0].strategy_id == "ensemble"
    assert out2[0].metadata["votes"] == 2
    assert set(out2[0].metadata["contributors"]) == {"a", "b"}


def test_confirmation_filter_window_expiry() -> None:
    f = ConfirmationFilter(min_votes=2, window=timedelta(minutes=5))
    now = datetime(2026, 5, 25, tzinfo=UTC)
    f.filter([_sig("a", Direction.LONG)], now=now)
    # 10 min later — out of window
    out = f.filter([_sig("b", Direction.LONG)], now=now + timedelta(minutes=10))
    assert out == []  # 'a' vote expired


def test_confirmation_does_not_double_promote() -> None:
    f = ConfirmationFilter(min_votes=2, window=timedelta(minutes=5))
    now = datetime(2026, 5, 25, tzinfo=UTC)
    f.filter([_sig("a", Direction.LONG)], now=now)
    out1 = f.filter([_sig("b", Direction.LONG)], now=now + timedelta(minutes=1))
    out2 = f.filter([_sig("c", Direction.LONG)], now=now + timedelta(minutes=2))
    assert len(out1) == 1  # first promotion
    assert len(out2) == 0  # already promoted in window


def test_hold_signals_pass_through_unchanged() -> None:
    f = ConfirmationFilter(min_votes=2)
    now = datetime(2026, 5, 25, tzinfo=UTC)
    out = f.filter([_sig("a", Direction.HOLD)], now=now)
    assert len(out) == 1
    assert out[0].direction is Direction.HOLD


def test_stop_opposing_signals() -> None:
    f = StopOpposingSignalsFilter()
    now = datetime(2026, 5, 25, tzinfo=UTC)
    out = f.filter(
        [_sig("a", Direction.LONG), _sig("b", Direction.SHORT)],
        now=now,
    )
    assert out == []
