"""Moving average tests with hand-computed reference values."""

from __future__ import annotations

import math

import pytest

from features import create
from tests.unit._indicator_helpers import bars_from_closes


def _feed(ind, bars):
    return [ind.update(b) for b in bars]


# ---- SMA -----


def test_sma_simple() -> None:
    sma = create("sma", period=3)
    bars = bars_from_closes([1, 2, 3, 4, 5])
    readings = _feed(sma, bars)
    assert [r.ready for r in readings] == [False, False, True, True, True]
    assert readings[2].values["value"] == pytest.approx(2.0)
    assert readings[3].values["value"] == pytest.approx(3.0)
    assert readings[4].values["value"] == pytest.approx(4.0)


# ---- EMA (SMA-seeded) -----


def test_ema_sma_seeded() -> None:
    ema = create("ema", period=3)
    bars = bars_from_closes([1, 2, 3, 4, 5])
    readings = _feed(ema, bars)
    # alpha = 2/(3+1) = 0.5; seed = SMA(1,2,3) = 2.0; then 0.5*4 + 0.5*2 = 3.0; then 0.5*5 + 0.5*3 = 4.0
    assert readings[2].ready and readings[2].values["value"] == pytest.approx(2.0)
    assert readings[3].values["value"] == pytest.approx(3.0)
    assert readings[4].values["value"] == pytest.approx(4.0)


# ---- DEMA & TEMA -----


def test_dema_warmup_then_value() -> None:
    dema = create("dema", period=3)
    bars = bars_from_closes(list(range(1, 21)))
    readings = _feed(dema, bars)
    # Some early readings carry math.nan but ready==False until 2N-1.
    assert dema.warmup == 5
    # After warmup, value should be a finite float close to the linear trend.
    final = readings[-1]
    assert final.ready and not math.isnan(final.values["value"])


def test_tema_warmup() -> None:
    tema = create("tema", period=3)
    assert tema.warmup == 7
    bars = bars_from_closes(list(range(1, 31)))
    readings = _feed(tema, bars)
    final = readings[-1]
    assert final.ready and not math.isnan(final.values["value"])


# ---- WMA -----


def test_wma_simple() -> None:
    wma = create("wma", period=3)
    bars = bars_from_closes([1, 2, 3, 4])
    readings = _feed(wma, bars)
    # On 3rd bar: (1*1 + 2*2 + 3*3) / (1+2+3) = 14/6 = 7/3
    assert readings[2].values["value"] == pytest.approx(14 / 6)
    # On 4th: (2*1 + 3*2 + 4*3) / 6 = 20/6
    assert readings[3].values["value"] == pytest.approx(20 / 6)


# ---- VWMA -----


def test_vwma_volume_weighted() -> None:
    from datetime import datetime, timedelta, timezone

    from core.schemas import AssetClass, Bar, BarTimeframe, Instrument
    from decimal import Decimal

    UTC = timezone.utc
    spy = Instrument(symbol="SPY", asset_class=AssetClass.ETF, venue="ARCA")
    base = datetime(2026, 5, 25, 13, 30, tzinfo=UTC)

    bars = [
        Bar(
            instrument=spy,
            timeframe=BarTimeframe.M1,
            timestamp=base + timedelta(minutes=i),
            open=Decimal(str(c)),
            high=Decimal(str(c + 0.1)),
            low=Decimal(str(c - 0.1)),
            close=Decimal(str(c)),
            volume=Decimal(str(v)),
        )
        for i, (c, v) in enumerate([(10, 1), (20, 1), (30, 4)])
    ]
    vwma = create("vwma", period=3)
    readings = _feed(vwma, bars)
    # SUM(c*v) / SUM(v) = (10*1 + 20*1 + 30*4) / 6 = (10+20+120)/6 = 150/6 = 25
    assert readings[2].values["value"] == pytest.approx(25.0)


# ---- ALMA -----


def test_alma_smoke() -> None:
    alma = create("alma", period=5)
    bars = bars_from_closes([10] * 10)  # constant input → constant output
    readings = _feed(alma, bars)
    # After warmup, ALMA of a constant series should equal that constant.
    assert readings[-1].ready
    assert readings[-1].values["value"] == pytest.approx(10.0)


# ---- LSMA -----


def test_lsma_recovers_slope() -> None:
    lsma = create("lsma", period=10)
    # Perfect line y = 2x + 5
    bars = bars_from_closes([2 * i + 5 for i in range(10)])
    readings = _feed(lsma, bars)
    final = readings[-1]
    assert final.ready
    # Endpoint should be 2*9 + 5 = 23
    assert final.values["value"] == pytest.approx(23.0)
    assert final.values["slope"] == pytest.approx(2.0)


# ---- HMA -----


def test_hma_smoke() -> None:
    hma = create("hma", period=4)
    bars = bars_from_closes([10] * 20)
    readings = _feed(hma, bars)
    assert readings[-1].ready
    # Constant input → constant output.
    assert readings[-1].values["value"] == pytest.approx(10.0)
