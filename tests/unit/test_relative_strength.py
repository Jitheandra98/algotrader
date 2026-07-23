"""Relative strength tests."""

from __future__ import annotations

import pytest

from core.schemas import AssetClass, Instrument
from features import RelativeStrength
from tests.unit._indicator_helpers import bars_from_closes


def _inst(symbol: str) -> Instrument:
    return Instrument(symbol=symbol, asset_class=AssetClass.EQUITY)


def test_benchmark_mode_outperformer_positive() -> None:
    rs = RelativeStrength(lookbacks=[5], benchmark="AAPL|EQUITY")
    aapl_bars = bars_from_closes([100, 101, 102, 103, 104, 105])  # +5%
    msft_bars = [
        b.model_copy(update={"instrument": _inst("MSFT")})
        for b in bars_from_closes([100, 102, 104, 106, 108, 110])  # +10%
    ]
    for ab, mb in zip(aapl_bars, msft_bars):
        ab2 = ab.model_copy(update={"instrument": _inst("AAPL")})
        rs.feed(ab2)
        rs.feed(mb)
    msft_reading = rs.reading(_inst("MSFT"))
    aapl_reading = rs.reading(_inst("AAPL"))
    assert msft_reading is not None and aapl_reading is not None
    # MSFT outperforms the benchmark (AAPL), so score > 0.
    assert msft_reading.score > 0
    # AAPL vs itself: score == 0.
    assert aapl_reading.score == pytest.approx(0.0)


def test_rank_mode_percentile() -> None:
    rs = RelativeStrength(lookbacks=[5], rank=True)
    closes_by_sym = {
        "A": [100, 100, 100, 100, 100, 100],   # 0%
        "B": [100, 101, 102, 103, 104, 105],   # +5%
        "C": [100, 102, 104, 106, 108, 110],   # +10%
    }
    for sym, closes in closes_by_sym.items():
        for b in bars_from_closes(closes):
            rs.feed(b.model_copy(update={"instrument": _inst(sym)}))
    readings = rs.all_readings()
    # C should rank highest (~99), A lowest (~1) within N=3.
    score_c = readings[_inst("C").key].score
    score_a = readings[_inst("A").key].score
    score_b = readings[_inst("B").key].score
    assert score_c > score_b > score_a


def test_invalid_weights_rejected() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        RelativeStrength(lookbacks=[5, 10], weights=[0.5, 0.6])
    with pytest.raises(ValueError, match="length"):
        RelativeStrength(lookbacks=[5, 10], weights=[1.0])
    with pytest.raises(ValueError, match="mutually exclusive"):
        RelativeStrength(lookbacks=[5], benchmark="SPY", rank=True)
