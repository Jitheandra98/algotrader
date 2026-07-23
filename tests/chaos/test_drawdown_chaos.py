"""Drawdown chaos: verify DD breach trips the kill-switch and risk engine
rejects all subsequent signals."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.schemas import AssetClass, Direction, Instrument, Signal
from monitoring import DrawdownCircuitBreaker
from risk import (
    FixedFractionalSizer,
    KillSwitch,
    RiskEngine,
)
from tests.unit._risk_helpers import StubRiskContext, make_inst

UTC = timezone.utc


def _signal() -> Signal:
    return Signal(
        strategy_id="s1",
        instrument=make_inst("SPY"),
        direction=Direction.LONG,
        conviction=1.0,
        rationale="t",
        stop_price=Decimal("95"),
    )


def test_drawdown_breach_trips_kill_switch_and_blocks_all_signals() -> None:
    ks = KillSwitch()
    risk = RiskEngine(
        sizer=FixedFractionalSizer(risk_per_trade_pct="0.005"),
        limits=[ks],  # KillSwitch is itself a limit
    )
    ctx = StubRiskContext(equity=Decimal("100000")).with_bar(make_inst("SPY"), close=100)

    # 1. Pre-breach: signal allowed
    decision = risk.decide(_signal(), ctx)
    assert decision.allowed

    # 2. DD breach: breaker trips the switch
    dd_breaker = DrawdownCircuitBreaker(kill_switch=ks, max_pct=5.0, get_drawdown_pct=lambda: -6.0)
    dd_breaker.evaluate()
    assert ks.is_tripped

    # 3. Post-breach: every signal blocked, with the kill-switch reason
    decision = risk.decide(_signal(), ctx)
    assert not decision.allowed
    assert any(r.limit == "kill_switch" for r in decision.rejections)
    assert any("drawdown" in r.reason.lower() for r in decision.rejections)


def test_manual_reset_restores_trading() -> None:
    ks = KillSwitch()
    ks.trip("operator manual")
    risk = RiskEngine(sizer=FixedFractionalSizer(risk_per_trade_pct="0.005"), limits=[ks])
    ctx = StubRiskContext(equity=Decimal("100000")).with_bar(make_inst("SPY"), close=100)

    assert not risk.decide(_signal(), ctx).allowed
    ks.reset()
    assert risk.decide(_signal(), ctx).allowed
