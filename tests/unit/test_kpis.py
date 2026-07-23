"""KPI computation tests with hand-verified values."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.kpis import compute_kpis
from backtest.portfolio import EquityPoint, TradeRecord
from core.schemas import AssetClass, Instrument

UTC = timezone.utc


def _curve(values: list[float], start: datetime | None = None) -> list[EquityPoint]:
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    return [
        EquityPoint(
            timestamp=start + timedelta(days=i),
            cash=Decimal(str(v)),
            positions_value=Decimal("0"),
            equity=Decimal(str(v)),
        )
        for i, v in enumerate(values)
    ]


def _inst() -> Instrument:
    return Instrument(symbol="X", asset_class=AssetClass.EQUITY)


def _trade(pnl: float, win: bool = True) -> TradeRecord:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return TradeRecord(
        instrument=_inst(),
        side="LONG",
        entry_time=start,
        exit_time=start + timedelta(days=2),
        entry_price=Decimal("100"),
        exit_price=Decimal(str(100 + pnl)),
        quantity=Decimal("1"),
        pnl=Decimal(str(pnl)),
        commission=Decimal("0"),
    )


def test_total_return_and_cagr() -> None:
    curve = _curve([100.0, 110.0])  # 1 day
    k = compute_kpis(curve, [])
    assert k.total_return_pct == pytest.approx(10.0)
    # CAGR over ~1/365 year of +10% is enormous; just check sign + finiteness
    assert k.cagr_pct > 10.0


def test_max_drawdown_basic() -> None:
    # Peak at idx 0 (100), trough at idx 2 (70). max DD = -30%.
    curve = _curve([100.0, 80.0, 70.0, 90.0, 100.0])
    k = compute_kpis(curve, [])
    assert k.max_drawdown_pct == pytest.approx(-30.0)


def test_win_rate_and_profit_factor() -> None:
    trades = [_trade(50), _trade(50), _trade(-25)]  # 2 wins, 1 loss
    curve = _curve([1000.0, 1075.0])
    k = compute_kpis(curve, trades)
    assert k.trades == 3
    assert k.wins == 2
    assert k.losses == 1
    assert k.win_rate_pct == pytest.approx(2 / 3 * 100)
    # PF = (50+50) / 25 = 4
    assert k.profit_factor == pytest.approx(4.0)
    # Expectancy = (50+50-25)/3 = 25
    assert k.expectancy == pytest.approx(25.0)


def test_consecutive_streaks() -> None:
    trades = [_trade(10), _trade(20), _trade(-5), _trade(15), _trade(-10), _trade(-20)]
    curve = _curve([1000.0, 1010.0])
    k = compute_kpis(curve, trades)
    assert k.max_consecutive_wins == 2
    assert k.max_consecutive_losses == 2


def test_sharpe_zero_when_no_variance() -> None:
    curve = _curve([100.0] * 10)
    k = compute_kpis(curve, [])
    assert k.sharpe == 0.0  # std = 0 → 0


def test_recovery_factor() -> None:
    # Final = 120, initial = 100, max DD = -50% of equity (=50 from a peak of 100)
    # Equity curve: 100 → 50 → 120
    curve = _curve([100.0, 50.0, 120.0])
    k = compute_kpis(curve, [])
    # net profit = 20; max DD% = -50 → recovery = abs(20 / (100 * -0.5)) = 0.4
    assert k.recovery_factor == pytest.approx(0.4)


def test_exposure_pct_passed_through() -> None:
    curve = _curve([100, 101, 102, 103, 100])
    k = compute_kpis(curve, [], bars_with_position=3)
    assert k.exposure_pct == pytest.approx(3 / 5 * 100)
