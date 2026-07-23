"""Research helpers — load fixtures, build datasets, render quick plots.

These are convenience wrappers for the most common research-notebook flows.
Import them in your notebook:

    from research.helpers import load_synthetic_spy, run_backtest_quick, plot_equity

"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from backtest import BacktestEngine, Portfolio, SimulatedBroker
from backtest.commission import PerShareCommission
from backtest.slippage import BpsSlippage
from backtest.synthetic import default_spy_instrument, synthetic_daily_bars
from core.schemas import Instrument
from strategies import StrategyConfig, create as create_strategy

UTC = timezone.utc


def load_synthetic_spy(*, years: float = 5.0, seed: int = 12345, **kw: Any) -> tuple[Instrument, list]:
    """Quick fixture: synthetic 5y daily SPY bars. Useful for prototyping."""
    inst = default_spy_instrument()
    bars = synthetic_daily_bars(
        inst,
        start=datetime(2021, 1, 1, tzinfo=UTC),
        years=years,
        seed=seed,
        **kw,
    )
    return inst, bars


def run_backtest_quick(
    *,
    strategy_name: str,
    parameters: dict[str, Any] | None = None,
    bars: list | None = None,
    initial_cash: str = "100000",
    bps_slippage: str = "5",
):
    """One-call backtest — handy in notebooks. Returns a :class:`BacktestResult`."""
    inst, default_bars = load_synthetic_spy() if bars is None else (None, bars)
    if inst is None:
        inst = bars[0].instrument
    strategy = create_strategy(
        strategy_name,
        StrategyConfig(name=f"{strategy_name}-research", instruments=[inst], parameters=parameters or {}),
    )
    engine = BacktestEngine(
        strategy=strategy,
        broker=SimulatedBroker(slippage=BpsSlippage(bps=bps_slippage), commission=PerShareCommission()),
        portfolio=Portfolio(initial_cash=initial_cash),
    )
    return engine.run(bars or default_bars)


def plot_equity(equity_curve, *, title: str = "Equity") -> None:
    """Tries matplotlib; falls back to printing a small ASCII summary."""
    try:
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]

        xs = [p.timestamp for p in equity_curve]
        ys = [float(p.equity) for p in equity_curve]
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(xs, ys)
        ax.set_title(title)
        ax.set_xlabel("Time")
        ax.set_ylabel("Equity")
        ax.grid(True, alpha=0.3)
        plt.show()
    except ImportError:
        # Headless fallback: top + bottom + dd.
        ys = [float(p.equity) for p in equity_curve]
        if not ys:
            print("(empty)")
            return
        peak = max(ys)
        trough = min(ys)
        last = ys[-1]
        dd = (last - peak) / peak * 100 if peak > 0 else 0
        print(f"{title}: start={ys[0]:.0f} end={last:.0f} peak={peak:.0f} trough={trough:.0f} (dd from peak {dd:+.2f}%)")
