"""RiskEngine pipeline tests + property test: no allowed trade violates a limit."""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.schemas import Direction, OrderSide, Signal
from risk import (
    FixedFractionalSizer,
    GrossLeverage,
    KillSwitch,
    MaxPositionSize,
    PerTradeRiskCap,
    RiskDecision,
    RiskEngine,
)
from tests.unit._risk_helpers import StubRiskContext, make_inst, make_position

UTC = timezone.utc


def _signal(direction=Direction.LONG, conviction=1.0, **kw) -> Signal:
    return Signal(
        strategy_id="s1",
        instrument=make_inst("SPY"),
        direction=direction,
        conviction=conviction,
        rationale="t",
        **kw,
    )


# ---- pipeline ----


def test_engine_allows_under_limits() -> None:
    eng = RiskEngine(
        sizer=FixedFractionalSizer(risk_per_trade_pct="0.01"),
        limits=[PerTradeRiskCap(max_pct="0.02"), MaxPositionSize(max_pct="0.20")],
    )
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(make_inst("SPY"), close=100)
    sig = _signal(stop_price=Decimal("95"))
    decision = eng.decide(sig, ctx)
    assert decision.allowed and decision.order is not None
    # risk = 100; risk_per_unit = 5 → qty 20
    assert decision.order.quantity == Decimal("20.0000")


def test_engine_blocks_when_limit_rejects() -> None:
    eng = RiskEngine(
        sizer=FixedFractionalSizer(risk_per_trade_pct="0.01"),
        limits=[PerTradeRiskCap(max_pct="0.005")],  # cap of 0.5%; sizer aims for 1% → blocked
    )
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(make_inst("SPY"), close=100)
    sig = _signal(stop_price=Decimal("95"))
    decision = eng.decide(sig, ctx)
    assert not decision.allowed
    assert decision.order is None
    assert len(decision.rejections) == 1
    assert decision.rejections[0].limit == "per_trade_risk_cap"


def test_engine_collects_all_rejections() -> None:
    """Multiple limits can reject the same trade — the engine should keep them all."""
    eng = RiskEngine(
        sizer=FixedFractionalSizer(target_alloc_pct="0.50"),  # aggressive sizing
        limits=[
            MaxPositionSize(max_pct="0.10"),     # would block
            GrossLeverage(max_leverage="0.05"),   # would also block
        ],
    )
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(make_inst("SPY"), close=100)
    decision = eng.decide(_signal(), ctx)
    assert not decision.allowed
    assert len(decision.rejections) == 2


def test_engine_handles_exit_signal() -> None:
    eng = RiskEngine(limits=[PerTradeRiskCap()])
    spy = make_inst("SPY")
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(spy, close=100)
        .with_position(make_position(spy, qty=10, avg_price=100, current_price=100))
    )
    decision = eng.decide(_signal(direction=Direction.EXIT), ctx)
    assert decision.allowed
    assert decision.order is not None
    assert decision.order.side is OrderSide.SELL
    assert decision.order.quantity == Decimal("10")


def test_engine_handles_hold_signal_as_noop() -> None:
    eng = RiskEngine()
    ctx = StubRiskContext().with_bar(make_inst("SPY"))
    decision = eng.decide(_signal(direction=Direction.HOLD), ctx)
    assert not decision.allowed
    assert decision.order is None


def test_engine_kill_switch_blocks_everything() -> None:
    ks = KillSwitch()
    eng = RiskEngine(limits=[ks])
    spy = make_inst("SPY")
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(spy, close=100)
    assert eng.decide(_signal(stop_price=Decimal("95")), ctx).allowed is True
    ks.trip("test")
    decision = eng.decide(_signal(stop_price=Decimal("95")), ctx)
    assert not decision.allowed
    assert any(r.limit == "kill_switch" for r in decision.rejections)


def test_engine_audit_log_captures_decisions() -> None:
    eng = RiskEngine(limits=[PerTradeRiskCap(max_pct="0.001")])  # block everything
    ctx = StubRiskContext(equity=Decimal("10000")).with_bar(make_inst("SPY"), close=100)
    for _ in range(5):
        eng.decide(_signal(stop_price=Decimal("90")), ctx)
    assert len(eng.audit_log) == 5
    assert all(not d.allowed for d in eng.audit_log)


# ---- property test: no allowed trade violates a limit ----


def test_property_allowed_trade_passes_all_limits() -> None:
    """For every combination of (signal stop, equity, limit caps), the engine's
    decision is sound: allowed ⇒ no limit's check() returns a rejection."""
    sizes = [Decimal("5000"), Decimal("10000"), Decimal("100000")]
    risk_pcts = ["0.005", "0.01", "0.02"]
    stop_dists = [Decimal("1"), Decimal("5"), Decimal("20")]
    leverage_caps = ["0.5", "1.0", "2.0"]
    n = 0
    spy = make_inst("SPY")

    for equity, risk_pct, stop_dist, lev in itertools.product(sizes, risk_pcts, stop_dists, leverage_caps):
        eng = RiskEngine(
            sizer=FixedFractionalSizer(risk_per_trade_pct=risk_pct),
            limits=[
                PerTradeRiskCap(max_pct="0.02"),
                MaxPositionSize(max_pct="0.30"),
                GrossLeverage(max_leverage=lev),
            ],
        )
        ctx = StubRiskContext(equity=equity).with_bar(spy, close=100)
        sig = _signal(stop_price=Decimal("100") - stop_dist)
        decision = eng.decide(sig, ctx)
        n += 1
        if decision.allowed:
            # Property: no limit may reject the same order in the same context.
            assert decision.order is not None
            for limit in eng.limits:
                rej = limit.check(decision.order, sig, ctx)
                assert rej is None, (
                    f"limit {limit.name} rejects an 'allowed' trade: {rej.reason}\n"
                    f"  equity={equity} risk_pct={risk_pct} stop_dist={stop_dist} lev={lev}\n"
                    f"  decision.sizing_meta={decision.sizing_meta}"
                )
        else:
            # Property: at least one rejection or sizer-zero must be recorded.
            assert decision.rejections or decision.sizing_meta.get("reason") in (
                "no price available",
                "sizer returned zero quantity",
            )
    assert n == 81
