"""Portfolio P&L invariants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backtest.portfolio import Portfolio
from core.schemas import (
    AssetClass,
    Bar,
    BarTimeframe,
    Fill,
    Instrument,
    OrderSide,
)

UTC = timezone.utc


def _inst() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF)


def _fill(side: OrderSide, qty: str, price: str, *, comm: str = "0", ts: datetime | None = None) -> Fill:
    return Fill(
        order_id=uuid4(),
        instrument=_inst(),
        side=side,
        quantity=Decimal(qty),
        price=Decimal(price),
        timestamp=ts or datetime(2026, 5, 25, 13, 30, tzinfo=UTC),
        commission=Decimal(comm),
    )


def test_open_long_then_close_realizes_pnl() -> None:
    pf = Portfolio(initial_cash="10000")
    pf.apply_fill(_fill(OrderSide.BUY, "10", "100"))
    assert pf.cash == Decimal("9000")
    assert pf.position(_inst()).quantity == 10

    # Close at 110 → PnL = (110-100)*10 = 100
    trade = pf.apply_fill(_fill(OrderSide.SELL, "10", "110"))
    assert trade is not None
    assert trade.pnl == Decimal("100")
    assert pf.cash == Decimal("10100")
    assert pf.position(_inst()) is None


def test_open_short_then_cover_realizes_pnl() -> None:
    pf = Portfolio(initial_cash="10000")
    pf.apply_fill(_fill(OrderSide.SELL, "10", "100"))  # short open
    assert pf.cash == Decimal("11000")  # short proceeds
    pos = pf.position(_inst())
    assert pos is not None and pos.quantity == -10

    # Cover at 90 → PnL = (100-90)*10 = 100
    trade = pf.apply_fill(_fill(OrderSide.BUY, "10", "90"))
    assert trade is not None
    assert trade.pnl == Decimal("100")
    assert pf.position(_inst()) is None


def test_adding_to_long_weighted_average() -> None:
    pf = Portfolio(initial_cash="10000")
    pf.apply_fill(_fill(OrderSide.BUY, "10", "100"))  # avg=100
    pf.apply_fill(_fill(OrderSide.BUY, "10", "120"))  # avg = (10*100 + 10*120)/20 = 110
    pos = pf.position(_inst())
    assert pos is not None
    assert pos.quantity == 20
    assert pos.average_price == Decimal("110")


def test_partial_reduction_realizes_proportional_pnl() -> None:
    pf = Portfolio(initial_cash="10000")
    pf.apply_fill(_fill(OrderSide.BUY, "10", "100"))
    trade = pf.apply_fill(_fill(OrderSide.SELL, "4", "110"))  # close 4
    assert trade is not None
    # PnL = (110-100)*4 = 40
    assert trade.pnl == Decimal("40")
    pos = pf.position(_inst())
    assert pos is not None
    assert pos.quantity == 6
    assert pos.average_price == Decimal("100")  # avg unchanged on reduction


def test_reversal_closes_and_opens() -> None:
    pf = Portfolio(initial_cash="10000")
    pf.apply_fill(_fill(OrderSide.BUY, "10", "100"))
    trade = pf.apply_fill(_fill(OrderSide.SELL, "15", "110"))  # close all + short 5
    assert trade is not None
    # PnL = (110-100)*10 = 100 (only on the closed portion)
    assert trade.pnl == Decimal("100")
    pos = pf.position(_inst())
    assert pos is not None
    assert pos.quantity == -5
    assert pos.average_price == Decimal("110")


def test_commission_reduces_cash() -> None:
    pf = Portfolio(initial_cash="10000")
    pf.apply_fill(_fill(OrderSide.BUY, "10", "100", comm="2"))
    # cash = 10000 - 1000 - 2 = 8998
    assert pf.cash == Decimal("8998")


def test_mark_bar_appends_equity_point() -> None:
    pf = Portfolio(initial_cash="10000")
    pf.apply_fill(_fill(OrderSide.BUY, "10", "100"))
    bar = Bar(
        instrument=_inst(),
        timeframe=BarTimeframe.D1,
        timestamp=datetime(2026, 5, 25, 13, 30, tzinfo=UTC),
        open=Decimal("100"), high=Decimal("110"), low=Decimal("99"), close=Decimal("110"),
        volume=Decimal("1000"),
    )
    pf.mark_bar(bar)
    curve = pf.equity_curve
    assert len(curve) == 1
    # cash=9000, position_value = 10 * 110 = 1100 → equity = 10100
    assert curve[0].equity == Decimal("10100")
