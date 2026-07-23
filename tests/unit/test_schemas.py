"""Schema validation tests.

These lock the invariants that downstream layers depend on. If any of these fail, the
boundary contract is broken and something in core/schemas.py was loosened by mistake.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.schemas import (
    AssetClass,
    Bar,
    BarTimeframe,
    Direction,
    Fill,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Signal,
    Tick,
    TimeInForce,
)


# ---------- Instrument ----------


def test_instrument_normalizes_symbol() -> None:
    inst = Instrument(symbol="spy", asset_class=AssetClass.ETF)
    assert inst.symbol == "SPY"


def test_instrument_option_requires_full_chain_fields() -> None:
    with pytest.raises(ValidationError):
        Instrument(symbol="AAPL", asset_class=AssetClass.OPTION)


def test_instrument_option_accepts_valid_chain(aapl_call: Instrument) -> None:
    assert aapl_call.option_right == "C"
    assert aapl_call.strike == Decimal("200")
    assert "OPT" in aapl_call.key


def test_instrument_is_frozen(spy: Instrument) -> None:
    with pytest.raises(ValidationError):
        spy.symbol = "QQQ"  # type: ignore[misc]


def test_instrument_key_stable_across_classes(spy: Instrument, btc_usd: Instrument) -> None:
    assert spy.key == "SPY|ETF"
    assert btc_usd.key == "BTC/USD|CRYPTO"


# ---------- Tick ----------


def test_tick_rejects_naive_datetime(spy: Instrument) -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Tick(instrument=spy, timestamp=datetime(2026, 5, 25, 10, 0), last_price=Decimal("100"))


def test_tick_requires_one_price(spy: Instrument, utc_now: datetime) -> None:
    with pytest.raises(ValidationError, match="must include"):
        Tick(instrument=spy, timestamp=utc_now)


def test_tick_coerces_non_utc_to_utc(spy: Instrument) -> None:
    from datetime import timedelta

    eastern_zone = timezone(timedelta(hours=-5))
    ts = datetime(2026, 5, 25, 10, 0, tzinfo=eastern_zone)
    tick = Tick(instrument=spy, timestamp=ts, last_price=Decimal("100"))
    assert tick.timestamp.tzinfo == timezone.utc


# ---------- Bar ----------


def test_bar_ohlc_ordering_enforced(spy: Instrument, utc_now: datetime) -> None:
    with pytest.raises(ValidationError, match="OHLC ordering"):
        Bar(
            instrument=spy,
            timeframe=BarTimeframe.M1,
            timestamp=utc_now,
            open=Decimal("100"),
            high=Decimal("99"),  # high < open
            low=Decimal("98"),
            close=Decimal("100"),
            volume=Decimal("1000"),
        )


def test_bar_accepts_valid_ohlc(spy: Instrument, utc_now: datetime) -> None:
    bar = Bar(
        instrument=spy,
        timeframe=BarTimeframe.M1,
        timestamp=utc_now,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99.5"),
        close=Decimal("100.5"),
        volume=Decimal("12345"),
    )
    assert bar.close == Decimal("100.5")


# ---------- Signal ----------


def test_signal_requires_rationale(spy: Instrument) -> None:
    with pytest.raises(ValidationError):
        Signal(
            strategy_id="rsi_mr",
            instrument=spy,
            direction=Direction.LONG,
            conviction=0.7,
            rationale="",
        )


def test_signal_conviction_in_unit_interval(spy: Instrument) -> None:
    with pytest.raises(ValidationError):
        Signal(
            strategy_id="rsi_mr",
            instrument=spy,
            direction=Direction.LONG,
            conviction=1.5,
            rationale="oversold",
        )


def test_signal_is_immutable(spy: Instrument) -> None:
    sig = Signal(
        strategy_id="rsi_mr",
        instrument=spy,
        direction=Direction.LONG,
        conviction=0.7,
        rationale="oversold",
    )
    with pytest.raises(ValidationError):
        sig.conviction = 0.5  # type: ignore[misc]


# ---------- Order ----------


def test_order_limit_requires_limit_price(spy: Instrument) -> None:
    with pytest.raises(ValidationError, match="requires limit_price"):
        Order(
            client_order_id="c1",
            instrument=spy,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("10"),
            strategy_id="s1",
            broker="alpaca",
        )


def test_order_stop_requires_stop_price(spy: Instrument) -> None:
    with pytest.raises(ValidationError, match="requires stop_price"):
        Order(
            client_order_id="c1",
            instrument=spy,
            side=OrderSide.SELL,
            order_type=OrderType.STOP,
            quantity=Decimal("10"),
            strategy_id="s1",
            broker="alpaca",
        )


def test_order_market_needs_neither(spy: Instrument) -> None:
    o = Order(
        client_order_id="c1",
        instrument=spy,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("10"),
        strategy_id="s1",
        broker="alpaca",
    )
    assert o.status == OrderStatus.PENDING_NEW
    assert o.time_in_force == TimeInForce.DAY


def test_order_round_trip_json(spy: Instrument) -> None:
    o = Order(
        client_order_id="c1",
        instrument=spy,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("100.50"),
        strategy_id="s1",
        broker="alpaca",
    )
    blob = o.model_dump_json()
    o2 = Order.model_validate_json(blob)
    assert o2 == o


# ---------- Fill ----------


def test_fill_decimal_price(spy: Instrument, utc_now: datetime) -> None:
    from uuid import uuid4

    f = Fill(
        order_id=uuid4(),
        instrument=spy,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.123456"),
        timestamp=utc_now,
        commission=Decimal("0.01"),
    )
    assert f.price == Decimal("100.123456")


# ---------- Position ----------


def test_position_classification(spy: Instrument) -> None:
    long_p = Position(instrument=spy, quantity=Decimal("10"), average_price=Decimal("100"))
    short_p = Position(instrument=spy, quantity=Decimal("-5"), average_price=Decimal("100"))
    flat = Position(instrument=spy, quantity=Decimal("0"), average_price=Decimal("100"))

    assert long_p.is_long
    assert short_p.is_short
    assert flat.is_flat
    assert long_p.notional == Decimal("1000")
