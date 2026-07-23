"""Slippage and commission model tests."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4
from datetime import datetime, timezone

import pytest

from backtest.commission import (
    PercentCommission,
    PerShareCommission,
    PerTradeCommission,
    ZeroCommission,
)
from backtest.slippage import (
    AtrSlippage,
    BpsSlippage,
    PercentSlippage,
    ZeroSlippage,
)
from core.schemas import AssetClass, Fill, Instrument, OrderSide

UTC = timezone.utc


def _inst() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF)


# ---- Slippage ----


def test_zero_slippage() -> None:
    s = ZeroSlippage()
    assert s.slip(instrument=_inst(), side=OrderSide.BUY, reference_price=Decimal("100"), quantity=Decimal("1")) == 0


def test_percent_slippage_buy_positive() -> None:
    s = PercentSlippage(pct="0.01")  # 1%
    out = s.slip(instrument=_inst(), side=OrderSide.BUY, reference_price=Decimal("100"), quantity=Decimal("1"))
    assert out == Decimal("1.00")


def test_percent_slippage_sell_negative() -> None:
    s = PercentSlippage(pct="0.01")
    out = s.slip(instrument=_inst(), side=OrderSide.SELL, reference_price=Decimal("100"), quantity=Decimal("1"))
    assert out == Decimal("-1.00")


def test_bps_slippage() -> None:
    s = BpsSlippage(bps="50")  # 50 bps = 0.5%
    out = s.slip(instrument=_inst(), side=OrderSide.BUY, reference_price=Decimal("100"), quantity=Decimal("1"))
    assert out == Decimal("0.5")


def test_atr_slippage_uses_atr_when_present() -> None:
    s = AtrSlippage(factor="0.5")
    out = s.slip(
        instrument=_inst(),
        side=OrderSide.BUY,
        reference_price=Decimal("100"),
        quantity=Decimal("1"),
        atr=Decimal("2"),
    )
    assert out == Decimal("1.0")  # 0.5 * 2


def test_atr_slippage_fallback_when_no_atr() -> None:
    s = AtrSlippage(factor="0.5", fallback_bps="10")  # 10 bps fallback
    out = s.slip(
        instrument=_inst(),
        side=OrderSide.BUY,
        reference_price=Decimal("100"),
        quantity=Decimal("1"),
        atr=None,
    )
    # 10 bps of 100 = 0.1
    assert out == Decimal("0.1")


def test_negative_slippage_params_rejected() -> None:
    with pytest.raises(ValueError):
        PercentSlippage(pct="-0.01")
    with pytest.raises(ValueError):
        BpsSlippage(bps="-1")


# ---- Commission ----


def _fill(price: str, qty: str) -> Fill:
    return Fill(
        order_id=uuid4(),
        instrument=_inst(),
        side=OrderSide.BUY,
        quantity=Decimal(qty),
        price=Decimal(price),
        timestamp=datetime(2026, 5, 25, 13, 30, tzinfo=UTC),
    )


def test_zero_commission() -> None:
    assert ZeroCommission().commission(_fill("100", "10")) == 0


def test_per_share_commission_minimum_floor() -> None:
    # 0.005 * 10 = 0.05; floor at 1.00.
    c = PerShareCommission(per_share="0.005", minimum="1.00", maximum_pct="0")
    assert c.commission(_fill("100", "10")) == Decimal("1.00")


def test_per_share_commission_normal() -> None:
    # 0.005 * 1000 = 5.00
    c = PerShareCommission(per_share="0.005", minimum="1.00", maximum_pct="0")
    assert c.commission(_fill("50", "1000")) == Decimal("5.00")


def test_per_share_commission_max_cap() -> None:
    # gross = 100*1000 = 100,000. 0.01% cap = 10. raw = 5. min = 1. cap kicks in only
    # if cap < raw. Here cap=10, raw=5 → return raw (=5).
    c = PerShareCommission(per_share="0.005", minimum="1.00", maximum_pct="0.0001")
    assert c.commission(_fill("100", "1000")) == Decimal("5.00")


def test_per_trade_commission() -> None:
    c = PerTradeCommission(per_trade="1.50")
    assert c.commission(_fill("100", "10")) == Decimal("1.50")


def test_percent_commission() -> None:
    # 0.1% of 100*10 = 1.0
    c = PercentCommission(pct="0.001")
    assert c.commission(_fill("100", "10")) == Decimal("1.000")
