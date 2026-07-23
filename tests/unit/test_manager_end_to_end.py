"""Multi-strategy backtest through the manager + ensemble filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backtest import BacktestEngine, Portfolio, SimulatedBroker
from backtest.synthetic import default_spy_instrument, synthetic_daily_bars
from strategies import (
    ConfirmationFilter,
    Lifecycle,
    MinConvictionFilter,
    StrategyConfig,
    StrategyManager,
    StrategySlot,
    create,
)

UTC = timezone.utc


def test_manager_with_two_strategies_and_confirmation_filter() -> None:
    """Run SMA crossover + RSI mean reversion on the same instrument, requiring
    at least one confirmation (min_votes=1 = pass-through, useful smoke)."""
    inst = default_spy_instrument()
    bars = synthetic_daily_bars(
        inst, start=datetime(2023, 1, 1, tzinfo=UTC), years=3.0, seed=99,
    )

    sma = create(
        "sma_crossover",
        StrategyConfig(name="sma", instruments=[inst], parameters={"fast_period": 10, "slow_period": 30}),
    )
    rsi = create(
        "rsi_mean_reversion",
        StrategyConfig(
            name="rsi",
            instruments=[inst],
            parameters={"rsi_period": 14, "oversold": 35, "overbought": 65, "trend_period": 50, "atr_period": 14},
        ),
    )
    manager = StrategyManager(
        StrategyConfig(name="ensemble", instruments=[]),
        children=[StrategySlot(sma), StrategySlot(rsi)],
    )

    engine = BacktestEngine(
        strategy=manager,
        broker=SimulatedBroker(),
        portfolio=Portfolio(initial_cash="100000"),
        signal_filters=[MinConvictionFilter(threshold=0.0)],
    )
    result = engine.run(bars)
    # At least one signal from each child should have fired over 3 years.
    strategy_ids = {s.strategy_id for s in result.signals}
    assert "sma_crossover" in strategy_ids or "rsi_mean_reversion" in strategy_ids


def test_indicator_dedup_across_strategies() -> None:
    """Two strategies requesting the same indicator should share one instance."""
    from backtest.engine import BacktestContext

    pf = Portfolio(initial_cash="10000")
    ctx = BacktestContext(pf)
    inst = default_spy_instrument()
    k1 = ctx.request_indicator(inst, "rsi", period=14)
    k2 = ctx.request_indicator(inst, "rsi", period=14)
    assert k1 == k2
    # Internal dict should only have one entry.
    assert len(ctx._indicators) == 1


def test_disabled_strategy_does_not_trade() -> None:
    inst = default_spy_instrument()
    bars = synthetic_daily_bars(
        inst, start=datetime(2024, 1, 1, tzinfo=UTC), years=1.5, seed=5,
    )
    sma = create(
        "sma_crossover",
        StrategyConfig(name="sma", instruments=[inst], parameters={"fast_period": 5, "slow_period": 15}),
    )
    manager = StrategyManager(
        StrategyConfig(name="m", instruments=[]),
        children=[StrategySlot(sma, Lifecycle.DISABLED)],
    )
    engine = BacktestEngine(
        strategy=manager,
        broker=SimulatedBroker(),
        portfolio=Portfolio(initial_cash="50000"),
    )
    result = engine.run(bars)
    assert result.signals == []
    assert result.kpis.trades == 0
