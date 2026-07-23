"""Walk-forward windowing + Monte Carlo sanity tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backtest.monte_carlo import monte_carlo_paths
from backtest.portfolio import TradeRecord
from backtest.walk_forward import WalkForwardRunner
from core.schemas import AssetClass, Instrument

UTC = timezone.utc


def test_rolling_window_count_and_sizes() -> None:
    wf = WalkForwardRunner(is_size=50, oos_size=10)
    data = list(range(200))
    windows = list(wf.windows(data))
    # First IS spans 0..49, OOS 50..59. Step = oos_size = 10.
    # Last starts at 140; OOS ends at 199 inclusive (200 exclusive).
    assert len(windows) == 15
    assert windows[0].in_sample == list(range(0, 50))
    assert windows[0].out_of_sample == list(range(50, 60))
    assert windows[-1].in_sample == list(range(140, 190))
    assert windows[-1].out_of_sample == list(range(190, 200))


def test_anchored_window_grows() -> None:
    wf = WalkForwardRunner(is_size=20, oos_size=10, anchored=True)
    data = list(range(60))
    windows = list(wf.windows(data))
    # Anchored: IS starts at 0 each time, IS_end grows by step.
    assert windows[0].in_sample == list(range(0, 20))
    assert windows[1].in_sample == list(range(0, 30))
    assert windows[2].in_sample == list(range(0, 40))


def _trade(pnl: float) -> TradeRecord:
    inst = Instrument(symbol="X", asset_class=AssetClass.EQUITY)
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    return TradeRecord(
        instrument=inst,
        side="LONG",
        entry_time=ts,
        exit_time=ts + timedelta(days=2),
        entry_price=Decimal("100"),
        exit_price=Decimal("100"),
        quantity=Decimal("1"),
        pnl=Decimal(str(pnl)),
        commission=Decimal("0"),
    )


def test_monte_carlo_trade_shuffle_invariant_total() -> None:
    trades = [_trade(10), _trade(-5), _trade(20), _trade(-3), _trade(8)]  # total = 30
    out = monte_carlo_paths(
        method="trade_shuffle",
        n_paths=500,
        initial_equity=1000,
        trades=trades,
        seed=1,
    )
    # Total return is invariant under shuffling: 30 / 1000 = 3% always (within
    # float rounding noise).
    assert out.final_return_pct["min"] == pytest.approx(3.0, abs=1e-9)
    assert out.final_return_pct["max"] == pytest.approx(3.0, abs=1e-9)
    # But max DD varies.
    assert out.max_drawdown_pct["p5"] <= out.max_drawdown_pct["p95"]


def test_monte_carlo_block_bootstrap_runs() -> None:
    returns = [0.01, -0.005, 0.008, -0.002, 0.012] * 50  # 250 returns
    out = monte_carlo_paths(
        method="block_bootstrap",
        n_paths=200,
        initial_equity=10000,
        bar_returns=returns,
        block_size=10,
        seed=42,
    )
    assert out.n_paths == 200
    assert "p50" in out.final_return_pct
    assert out.sharpe is not None
