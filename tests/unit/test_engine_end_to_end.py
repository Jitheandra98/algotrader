"""End-to-end backtest: SMA crossover on 5 years of synthetic daily SPY bars,
asserting the engine wiring produces a coherent tear sheet."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from backtest import (
    BacktestEngine,
    Portfolio,
    SimulatedBroker,
    monte_carlo_paths,
    render_tearsheet,
)
from backtest.commission import PerShareCommission
from backtest.slippage import BpsSlippage
from backtest.synthetic import default_spy_instrument, synthetic_daily_bars
from strategies import SmaCrossoverStrategy
from strategies.base import StrategyConfig

UTC = timezone.utc


def test_five_year_sma_crossover_produces_tearsheet(tmp_path: Path) -> None:
    inst = default_spy_instrument()
    bars = synthetic_daily_bars(
        inst,
        start=datetime(2021, 1, 1, tzinfo=UTC),
        years=5.0,
        initial_price=400.0,
        annual_drift=0.08,
        annual_vol=0.18,
        seed=12345,
    )
    assert len(bars) > 1000  # ~252 * 5 trading days

    strategy = SmaCrossoverStrategy(
        StrategyConfig(
            name="sma_crossover_test",
            instruments=[inst],
            parameters={"fast_period": 20, "slow_period": 50},
        )
    )
    broker = SimulatedBroker(
        slippage=BpsSlippage(bps="5"),
        commission=PerShareCommission(per_share="0.005", minimum="1.00", maximum_pct="0.01"),
    )
    portfolio = Portfolio(initial_cash="100000")

    engine = BacktestEngine(strategy=strategy, broker=broker, portfolio=portfolio)
    result = engine.run(bars)

    # Sanity invariants
    assert result.kpis.bars == len(bars)
    assert result.kpis.initial_equity == 100_000.0
    # At least some trades should fire over 5 years of crossover.
    assert result.kpis.trades > 5
    # Equity curve is the right length.
    assert len(portfolio.equity_curve) == len(bars)

    # Tear sheet renders.
    out_path = tmp_path / "tearsheet.md"
    md = render_tearsheet(
        title="SMA Crossover (test)",
        kpis=result.kpis,
        equity_curve=portfolio.equity_curve,
        trades=result.trades,
        output_path=out_path,
    )
    assert out_path.exists()
    text = out_path.read_text()
    assert "## Summary" in text
    assert "## Risk-Adjusted" in text
    assert "## Trade Statistics" in text
    assert f"Trades | {result.kpis.trades}" in text or f"| Trades | {result.kpis.trades}" in text


def test_signals_produce_orders_through_risk_layer() -> None:
    """Smoke test that signals → orders → fills loop closes end-to-end on a tiny
    deterministic series."""
    inst = default_spy_instrument()
    bars = synthetic_daily_bars(
        inst, start=datetime(2026, 1, 1, tzinfo=UTC), years=0.6, seed=7
    )
    strategy = SmaCrossoverStrategy(
        StrategyConfig(
            name="sma_xover",
            instruments=[inst],
            parameters={"fast_period": 5, "slow_period": 20},
        )
    )
    engine = BacktestEngine(
        strategy=strategy,
        broker=SimulatedBroker(),
        portfolio=Portfolio(initial_cash="50000"),
    )
    result = engine.run(bars)
    assert result.signals  # at least one signal got emitted
    assert result.fills    # at least one fill came back
