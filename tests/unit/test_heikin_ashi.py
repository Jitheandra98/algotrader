"""Heikin-Ashi aggregator tests.

Hand-verifies the recurrence against the documented formulas. If these change, HA
output is wrong.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from core.schemas import AssetClass, Bar, BarTimeframe, Instrument
from data.aggregators import HeikinAshiAggregator

UTC = timezone.utc


def _spy() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")


def _bar(ts: datetime, o: str, h: str, l: str, c: str, v: str = "1000") -> Bar:
    return Bar(
        instrument=_spy(),
        timeframe=BarTimeframe.M1,
        timestamp=ts,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(v),
    )


def test_first_bar_uses_oc_average_for_open() -> None:
    agg = HeikinAshiAggregator()
    ts = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    out = agg.on_bar(_bar(ts, "100", "105", "99", "102"))
    assert len(out) == 1
    ha = out[0]
    # HA_close = (100+105+99+102)/4 = 101.5
    assert ha.close == Decimal("101.5")
    # HA_open = (100+102)/2 = 101 (first bar)
    assert ha.open == Decimal("101")
    # HA_high = max(105, 101, 101.5) = 105
    assert ha.high == Decimal("105")
    # HA_low = min(99, 101, 101.5) = 99
    assert ha.low == Decimal("99")
    # Volume passes through
    assert ha.volume == Decimal("1000")


def test_second_bar_uses_prev_ha_for_open() -> None:
    agg = HeikinAshiAggregator()
    ts0 = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)
    ts1 = datetime(2026, 5, 25, 13, 31, tzinfo=UTC)
    agg.on_bar(_bar(ts0, "100", "105", "99", "102"))  # prev HA: open=101, close=101.5
    out = agg.on_bar(_bar(ts1, "102", "106", "101", "105"))
    assert len(out) == 1
    ha = out[0]
    # HA_close = (102+106+101+105)/4 = 414/4 = 103.5
    assert ha.close == Decimal("103.5")
    # HA_open = (prev_HA_open + prev_HA_close)/2 = (101 + 101.5)/2 = 101.25
    assert ha.open == Decimal("101.25")
    # HA_high = max(106, 101.25, 103.5) = 106
    assert ha.high == Decimal("106")
    # HA_low = min(101, 101.25, 103.5) = 101
    assert ha.low == Decimal("101")


def test_instruments_isolated() -> None:
    agg = HeikinAshiAggregator()
    spy_bar = _bar(datetime(2026, 5, 25, 13, 30, tzinfo=UTC), "100", "105", "99", "102")
    qqq_inst = Instrument(symbol="QQQ", asset_class=AssetClass.ETF)
    qqq_bar = Bar(
        instrument=qqq_inst,
        timeframe=BarTimeframe.M1,
        timestamp=datetime(2026, 5, 25, 13, 30, tzinfo=UTC),
        open=Decimal("480"),
        high=Decimal("482"),
        low=Decimal("479"),
        close=Decimal("481"),
        volume=Decimal("100"),
    )
    # Each instrument should track its own state.
    out_spy = agg.on_bar(spy_bar)
    out_qqq = agg.on_bar(qqq_bar)
    assert out_spy[0].instrument.symbol == "SPY"
    assert out_qqq[0].instrument.symbol == "QQQ"
