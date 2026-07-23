"""Registry + lifecycle tests."""

from __future__ import annotations

import pytest

from core.schemas import AssetClass, Instrument
from strategies import (
    Lifecycle,
    REGISTRY,
    SmaCrossoverStrategy,
    Strategy,
    StrategyConfig,
    create,
    names,
)


def _inst() -> Instrument:
    return Instrument(symbol="SPY", asset_class=AssetClass.ETF)


def test_registry_has_eight_strategies() -> None:
    assert set(names()) == {
        "bollinger_squeeze",
        "covered_call",
        "fvg_fill",
        "pair_trading",
        "rsi_mean_reversion",
        "sma_crossover",
        "td_sequential_reversal",
        "vcp_breakout",
    }


def test_create_sma_crossover_via_registry() -> None:
    s = create(
        "sma_crossover",
        StrategyConfig(
            name="t", instruments=[_inst()],
            parameters={"fast_period": 5, "slow_period": 20},
        ),
    )
    assert isinstance(s, SmaCrossoverStrategy)


def test_unknown_strategy_raises() -> None:
    with pytest.raises(KeyError, match="unknown strategy"):
        create("does_not_exist", StrategyConfig(name="t", instruments=[_inst()]))


def test_double_registration_raises() -> None:
    from strategies.registry import register

    @register("temp_test_only_xyz")
    def _f(cfg: StrategyConfig) -> Strategy:
        return SmaCrossoverStrategy(cfg)

    with pytest.raises(ValueError, match="already registered"):
        @register("temp_test_only_xyz")
        def _f2(cfg: StrategyConfig) -> Strategy:
            return SmaCrossoverStrategy(cfg)

    # Cleanup so other tests aren't polluted.
    REGISTRY.pop("temp_test_only_xyz", None)


def test_lifecycle_enum() -> None:
    assert Lifecycle.DISABLED.value == "DISABLED"
    assert Lifecycle.PAPER.value == "PAPER"
    assert Lifecycle.LIVE.value == "LIVE"


def test_covered_call_stub_raises() -> None:
    """The covered_call stub must raise on construction so accidental wiring
    fails loud before going to paper/live."""
    with pytest.raises(NotImplementedError, match="option-chain data"):
        create("covered_call", StrategyConfig(name="cc", instruments=[_inst()]))
