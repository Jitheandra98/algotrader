"""Individual limit tests with a stub RiskContext."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.schemas import (
    AssetClass,
    Bar,
    BarTimeframe,
    Direction,
    Order,
    OrderSide,
    OrderType,
    Signal,
)
from risk import (
    AggregateGreeksBound,
    ConsecutiveLossKill,
    CorrelationLimit,
    DailyDrawdownLimit,
    GrossLeverage,
    KillSwitch,
    LotMethod,
    LotSelector,
    MaxPositionSize,
    NetLeverage,
    PatternDayTrader,
    PerStrategyAllocation,
    PerTradeRiskCap,
    SectorExposureLimit,
    ShortSaleRestriction,
    TaxLot,
    WashSaleGuard,
    WeeklyDrawdownLimit,
)
from risk.limits.greeks import Greeks
from tests.unit._risk_helpers import StubRiskContext, make_inst, make_position

UTC = timezone.utc


def _order(qty="10", side=OrderSide.BUY) -> Order:
    return Order(
        client_order_id="t",
        instrument=make_inst("SPY"),
        side=side,
        order_type=OrderType.MARKET,
        quantity=Decimal(qty),
        strategy_id="s1",
        broker="sim",
    )


def _signal(direction=Direction.LONG, **kw) -> Signal:
    return Signal(
        strategy_id="s1",
        instrument=make_inst("SPY"),
        direction=direction,
        conviction=1.0,
        rationale="t",
        **kw,
    )


# ----- per_trade -----


def test_per_trade_risk_cap_blocks_when_exceeded() -> None:
    limit = PerTradeRiskCap(max_pct="0.01")
    sig = _signal(stop_price=Decimal("90"))   # entry 100, stop 90 → $10/unit risk
    ord_ = _order(qty="200")                   # $2000 risk
    ctx = StubRiskContext(equity=Decimal("100000")).with_bar(make_inst("SPY"), close=100)
    rej = limit.check(ord_, sig, ctx)
    # cap = $1000; $2000 > $1000 → reject
    assert rej is not None
    assert "per-trade" in rej.reason


def test_per_trade_risk_cap_passes_when_under() -> None:
    limit = PerTradeRiskCap(max_pct="0.01")
    sig = _signal(stop_price=Decimal("98"))
    ord_ = _order(qty="100")  # risk = 2*100 = 200
    ctx = StubRiskContext(equity=Decimal("100000")).with_bar(make_inst("SPY"), close=100)
    assert limit.check(ord_, sig, ctx) is None


def test_max_position_size_includes_existing() -> None:
    limit = MaxPositionSize(max_pct="0.20")
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(make_inst("SPY"), close=100)
        .with_position(make_position(make_inst("SPY"), qty=15, avg_price=100))
    )
    # existing notional 1500, adding 10 more = 1000 → 2500 > 2000 cap
    rej = limit.check(_order(qty="10"), _signal(), ctx)
    assert rej is not None


# ----- per_strategy -----


def test_per_strategy_allocation_caps_strategy_exposure() -> None:
    limit = PerStrategyAllocation(allocations={"s1": "0.10"})
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(make_inst("SPY"), close=100)
        .with_position(make_position(make_inst("SPY"), qty=8, avg_price=100, current_price=100, strategy_id="s1"))
    )
    # used = 800, cap = 1000; adding 5*100=500 → 1300 > 1000 → reject
    rej = limit.check(_order(qty="5"), _signal(), ctx)
    assert rej is not None


# ----- leverage -----


def test_gross_leverage_blocks_above_cap() -> None:
    limit = GrossLeverage(max_leverage="1.0")
    other = make_inst("QQQ")
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(make_inst("SPY"), close=100)
        .with_position(make_position(other, qty=80, avg_price=100, current_price=100))  # $8000
    )
    rej = limit.check(_order(qty="30"), _signal(), ctx)
    # existing 8000 + new 3000 = 11000 vs 10000 equity → reject
    assert rej is not None


def test_net_leverage_blocks_at_long_cap() -> None:
    limit = NetLeverage(max_long="1.0", max_short="1.0")
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(make_inst("SPY"), close=100)
        .with_position(make_position(make_inst("QQQ"), qty=80, avg_price=100, current_price=100))
    )
    rej = limit.check(_order(qty="30"), _signal(), ctx)
    assert rej is not None


# ----- drawdown -----


def test_daily_drawdown_blocks_when_breached() -> None:
    limit = DailyDrawdownLimit(max_pct="0.03")
    now = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)
    session_open = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
    ctx = (
        StubRiskContext(now=now, equity=Decimal("9600"))
        .with_bar(make_inst("SPY"), close=100)
        .with_equity_snapshot(session_open, Decimal("10000"))
    )
    # 4% drawdown vs 3% cap → reject
    rej = limit.check(_order(), _signal(), ctx)
    assert rej is not None


def test_weekly_drawdown_passes_when_under() -> None:
    limit = WeeklyDrawdownLimit(max_pct="0.10")
    now = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)  # Mon
    monday = datetime(2026, 5, 25, 0, 0, tzinfo=UTC)
    ctx = (
        StubRiskContext(now=now, equity=Decimal("9700"))
        .with_bar(make_inst("SPY"), close=100)
        .with_equity_snapshot(monday, Decimal("10000"))
    )
    assert limit.check(_order(), _signal(), ctx) is None


# ----- kill switch -----


def test_kill_switch_trip_and_reset() -> None:
    ks = KillSwitch()
    ctx = StubRiskContext().with_bar(make_inst("SPY"))
    assert ks.check(_order(), _signal(), ctx) is None
    ks.trip("operator panic button")
    rej = ks.check(_order(), _signal(), ctx)
    assert rej is not None
    assert "operator panic button" in rej.reason
    ks.reset()
    assert ks.check(_order(), _signal(), ctx) is None


def test_consecutive_loss_kill_auto_trips() -> None:
    ck = ConsecutiveLossKill(max_consecutive=3)
    ctx = (
        StubRiskContext()
        .with_bar(make_inst("SPY"))
        .with_trade_pnls([Decimal("-1"), Decimal("-2"), Decimal("-3")])
    )
    rej = ck.check(_order(), _signal(), ctx)
    assert rej is not None


def test_consecutive_loss_kill_resets_after_cooldown() -> None:
    ck = ConsecutiveLossKill(max_consecutive=3, cooldown=timedelta(hours=1))
    base = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    ctx = (
        StubRiskContext(now=base)
        .with_bar(make_inst("SPY"))
        .with_trade_pnls([Decimal("-1"), Decimal("-2"), Decimal("-3")])
    )
    assert ck.check(_order(), _signal(), ctx) is not None
    # Two hours later — cooldown expired (and no new losing trades).
    ctx.now = base + timedelta(hours=2)
    ctx._trade_pnls = []  # cleared losses
    assert ck.check(_order(), _signal(), ctx) is None


# ----- PDT -----


def test_pdt_inactive_for_large_account() -> None:
    pdt = PatternDayTrader()
    ctx = StubRiskContext(account_value=Decimal("30000"), is_margin_account=True).with_bar(make_inst("SPY"))
    assert pdt.check(_order(), _signal(), ctx) is None


def test_pdt_inactive_for_cash_account() -> None:
    pdt = PatternDayTrader()
    ctx = StubRiskContext(account_value=Decimal("5000"), is_margin_account=False).with_bar(make_inst("SPY"))
    assert pdt.check(_order(), _signal(), ctx) is None


# ----- SSR -----


def test_ssr_blocks_short_on_non_uptick() -> None:
    ssr = ShortSaleRestriction()
    ctx = (
        StubRiskContext()
        .with_bar(make_inst("SPY"), open=101, close=99)   # down bar
        .with_ssr_flag(make_inst("SPY"))
    )
    rej = ssr.check(_order(side=OrderSide.SELL), _signal(), ctx)
    assert rej is not None


def test_ssr_allows_short_on_uptick() -> None:
    ssr = ShortSaleRestriction()
    ctx = (
        StubRiskContext()
        .with_bar(make_inst("SPY"), open=99, close=101)   # up bar
        .with_ssr_flag(make_inst("SPY"))
    )
    assert ssr.check(_order(side=OrderSide.SELL), _signal(), ctx) is None


def test_ssr_no_op_when_not_flagged() -> None:
    ssr = ShortSaleRestriction()
    ctx = StubRiskContext().with_bar(make_inst("SPY"), open=101, close=99)
    assert ssr.check(_order(side=OrderSide.SELL), _signal(), ctx) is None


# ----- sector -----


def test_sector_exposure_limit() -> None:
    limit = SectorExposureLimit(sector_pct={"TECH": "0.30"})
    spy = make_inst("SPY")
    qqq = make_inst("QQQ")
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(spy, close=100)
        .with_sector(spy, "TECH")
        .with_sector(qqq, "TECH")
        .with_position(make_position(qqq, qty=25, avg_price=100, current_price=100))  # 2500 in TECH
    )
    # cap = 3000; existing 2500 + new 10*100=1000 → 3500 > 3000 → reject
    rej = limit.check(_order(qty="10"), _signal(), ctx)
    assert rej is not None


# ----- correlation -----


def test_correlation_limit_blocks_high_corr_cluster() -> None:
    limit = CorrelationLimit(max_cluster_pct="0.30", rho_threshold=0.6)
    spy = make_inst("SPY")
    qqq = make_inst("QQQ")
    ctx = (
        StubRiskContext(equity=Decimal("10000"))
        .with_bar(spy, close=100)
        .with_position(make_position(qqq, qty=20, avg_price=100, current_price=100))  # 2000
        .with_correlation(spy, qqq, 0.9)
    )
    # weighted = 0.9 * 2000 = 1800; new = 20*100 = 2000; cluster = 3800; cap = 3000 → reject
    rej = limit.check(_order(qty="20"), _signal(), ctx)
    assert rej is not None


# ----- Greeks -----


def test_aggregate_greeks_bound_delta_only() -> None:
    limit = AggregateGreeksBound(max_abs_delta=Decimal("50"))
    spy = make_inst("SPY")
    ctx = (
        StubRiskContext()
        .with_bar(spy, close=100)
        .with_position(make_position(spy, qty=40, avg_price=100, current_price=100))
    )
    # default reader: delta = signed_qty. Adding 20 → portfolio delta = 60 > 50 → reject
    rej = limit.check(_order(qty="20"), _signal(), ctx)
    assert rej is not None


# ----- Wash sale -----


def test_wash_sale_guard_flags_recent_loss_but_allows_by_default() -> None:
    g = WashSaleGuard(block=False)
    spy = make_inst("SPY")
    g.record_close(spy, datetime(2026, 5, 1, tzinfo=UTC), Decimal("-100"))
    ctx = (
        StubRiskContext(now=datetime(2026, 5, 25, tzinfo=UTC))
        .with_bar(spy, close=100)
    )
    assert g.check(_order(side=OrderSide.BUY), _signal(), ctx) is None


def test_wash_sale_guard_blocks_when_configured() -> None:
    g = WashSaleGuard(block=True)
    spy = make_inst("SPY")
    g.record_close(spy, datetime(2026, 5, 1, tzinfo=UTC), Decimal("-100"))
    ctx = (
        StubRiskContext(now=datetime(2026, 5, 25, tzinfo=UTC))
        .with_bar(spy, close=100)
    )
    rej = g.check(_order(side=OrderSide.BUY), _signal(), ctx)
    assert rej is not None
    assert "wash-sale" in rej.reason


# ----- Lot selection -----


def test_fifo_lot_selection() -> None:
    sel = LotSelector(LotMethod.FIFO)
    spy = make_inst("SPY")
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    sel.add_lot(TaxLot(spy, Decimal("10"), Decimal("100"), t1, is_long=True))
    sel.add_lot(TaxLot(spy, Decimal("10"), Decimal("110"), t2, is_long=True))
    out = sel.close_lots(spy, Decimal("10"), Decimal("120"), datetime(2026, 6, 1, tzinfo=UTC))
    # FIFO: close the t1 lot first → realized = (120-100)*10 = 200
    assert len(out) == 1
    assert out[0][2] == Decimal("200")


def test_hifo_lot_selection_minimizes_short_term_gain() -> None:
    sel = LotSelector(LotMethod.HIFO)
    spy = make_inst("SPY")
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 2, 1, tzinfo=UTC)
    sel.add_lot(TaxLot(spy, Decimal("10"), Decimal("100"), t1, is_long=True))
    sel.add_lot(TaxLot(spy, Decimal("10"), Decimal("110"), t2, is_long=True))
    out = sel.close_lots(spy, Decimal("10"), Decimal("120"), datetime(2026, 6, 1, tzinfo=UTC))
    # HIFO: close the 110-basis lot first → realized = (120-110)*10 = 100
    assert out[0][2] == Decimal("100")
