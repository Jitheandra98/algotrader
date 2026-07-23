"""Sizer math tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.schemas import Direction, Signal
from risk import FixedFractionalSizer, KellySizer, VolTargetedSizer
from tests.unit._risk_helpers import StubRiskContext, make_inst

UTC = timezone.utc


def _signal(direction: Direction = Direction.LONG, **kw) -> Signal:
    return Signal(
        strategy_id="t",
        instrument=make_inst("SPY"),
        direction=direction,
        conviction=1.0,
        rationale="t",
        **kw,
    )


def test_fixed_fractional_with_stop() -> None:
    s = FixedFractionalSizer(risk_per_trade_pct="0.01")  # 1%
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(make_inst("SPY"), close=100)
    sig = _signal(stop_price=Decimal("95"))
    qty, meta = s.size(sig, ctx)
    # risk_amount = 100; risk_per_unit = 5 → qty = 20
    assert qty == Decimal("20.0000")
    assert meta["mode"] == "stop_based"


def test_fixed_fractional_alloc_fallback_when_no_stop() -> None:
    s = FixedFractionalSizer(target_alloc_pct="0.10")
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(make_inst("SPY"), close=100)
    qty, meta = s.size(_signal(), ctx)
    # alloc = 1000; qty = 10
    assert qty == Decimal("10.0000")
    assert meta["mode"] == "alloc_based"


def test_fixed_fractional_conviction_scales() -> None:
    s = FixedFractionalSizer(risk_per_trade_pct="0.01")
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(make_inst("SPY"), close=100)
    sig = _signal(stop_price=Decimal("95"))
    full = s.size(sig, ctx)[0]
    half = s.size(sig.model_copy(update={"conviction": 0.5}), ctx)[0]
    assert half == full / 2


def test_fixed_fractional_zero_for_exit() -> None:
    s = FixedFractionalSizer()
    ctx = StubRiskContext().with_bar(make_inst("SPY"))
    qty, _ = s.size(_signal(direction=Direction.EXIT), ctx)
    assert qty == 0


def test_vol_targeted_uses_atr() -> None:
    s = VolTargetedSizer(risk_per_trade_pct="0.01", atr_stop_multiple="2")
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(make_inst("SPY"), close=100)
        .with_atr(make_inst("SPY"), "2.5")
    )
    qty, meta = s.size(_signal(), ctx)
    # risk = 100; risk_per_unit = 2.5*2 = 5 → qty = 20
    assert qty == Decimal("20.0000")
    assert meta["mode"] == "atr_based"


def test_vol_targeted_fallback_without_atr() -> None:
    s = VolTargetedSizer(fallback_alloc_pct="0.05")
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(make_inst("SPY"), close=100)
    qty, meta = s.size(_signal(), ctx)
    assert meta["mode"] == "alloc_fallback"
    assert qty == Decimal("5.0000")


def test_kelly_uses_fallback_until_history_accrued() -> None:
    s = KellySizer(min_trades=30, fallback_alloc_pct="0.05")
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(make_inst("SPY"), close=100)
        .with_trade_pnls([Decimal("10")] * 5)  # only 5 trades < min 30
    )
    qty, meta = s.size(_signal(), ctx)
    assert meta["mode"] == "fallback_insufficient_history"
    assert qty == Decimal("5.0000")


def test_kelly_full_history_two_sided() -> None:
    s = KellySizer(kelly_fraction=0.5, min_trades=10)
    # 7 winners +20, 3 losers -10. win_rate=0.7, payoff=2.0
    # f* = 0.7 - 0.3/2 = 0.55; half-Kelly = 0.275; capped at max_pct=0.25.
    history = [Decimal("20")] * 7 + [Decimal("-10")] * 3
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(make_inst("SPY"), close=100)
        .with_trade_pnls(history)
    )
    qty, meta = s.size(_signal(), ctx)
    assert meta["f_star"] <= 0.25
    assert qty > 0


def test_negative_or_overlarge_params_rejected() -> None:
    with pytest.raises(ValueError):
        FixedFractionalSizer(risk_per_trade_pct="0.5")  # 50% way too big
    with pytest.raises(ValueError):
        VolTargetedSizer(atr_stop_multiple="0")
    with pytest.raises(ValueError):
        KellySizer(kelly_fraction=0)
