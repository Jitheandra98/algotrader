"""Monte Carlo — estimate distribution of outcomes from a single backtest.

Two flavors:
  - **trade_shuffle**: shuffle the order of historical trades. Total return is
    invariant; max drawdown distribution changes. Use to bound DD risk.
  - **block_bootstrap**: resample fixed-length blocks of bar-returns with
    replacement. Useful for return-distribution analyses where trade clumps matter.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass

from backtest.portfolio import EquityPoint, TradeRecord


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    n_paths: int
    final_return_pct: dict[str, float]  # percentile keys: "p5", "p50", "p95"
    max_drawdown_pct: dict[str, float]
    sharpe: dict[str, float] | None = None


def monte_carlo_paths(
    *,
    method: str = "trade_shuffle",
    n_paths: int = 1000,
    initial_equity: float = 100_000.0,
    trades: list[TradeRecord] | None = None,
    bar_returns: list[float] | None = None,
    block_size: int = 5,
    seed: int = 0,
    annualization_factor: float = 252.0,
) -> MonteCarloResult:
    """Run ``n_paths`` simulations and return percentile bands.

    Args:
        method: ``"trade_shuffle"`` (requires ``trades``) or
            ``"block_bootstrap"`` (requires ``bar_returns``).
        n_paths: number of simulated paths.
        initial_equity: starting equity in $.
        trades: trade list (for trade_shuffle).
        bar_returns: list of bar-to-bar return rates (for block_bootstrap).
        block_size: bars per block for bootstrap.
        seed: rng seed for reproducibility.
        annualization_factor: scales Sharpe for block_bootstrap.
    """
    rng = random.Random(seed)
    if method == "trade_shuffle":
        if trades is None:
            raise ValueError("trade_shuffle requires `trades`")
        return _trade_shuffle(rng, n_paths, initial_equity, trades)
    if method == "block_bootstrap":
        if bar_returns is None:
            raise ValueError("block_bootstrap requires `bar_returns`")
        return _block_bootstrap(rng, n_paths, initial_equity, bar_returns, block_size, annualization_factor)
    raise ValueError(f"unknown method: {method}")


def _trade_shuffle(
    rng: random.Random,
    n_paths: int,
    initial_equity: float,
    trades: list[TradeRecord],
) -> MonteCarloResult:
    pnls = [float(t.pnl) for t in trades]
    if not pnls:
        return MonteCarloResult(n_paths=0, final_return_pct={}, max_drawdown_pct={})

    final_returns: list[float] = []
    max_drawdowns: list[float] = []
    for _ in range(n_paths):
        shuffled = pnls.copy()
        rng.shuffle(shuffled)
        equity = initial_equity
        peak = equity
        worst_dd = 0.0
        for p in shuffled:
            equity += p
            if equity > peak:
                peak = equity
            elif peak > 0:
                dd = (equity / peak - 1.0) * 100.0
                if dd < worst_dd:
                    worst_dd = dd
        final_returns.append((equity / initial_equity - 1.0) * 100.0 if initial_equity > 0 else 0.0)
        max_drawdowns.append(worst_dd)

    return MonteCarloResult(
        n_paths=n_paths,
        final_return_pct=_percentiles(final_returns),
        max_drawdown_pct=_percentiles(max_drawdowns),
    )


def _block_bootstrap(
    rng: random.Random,
    n_paths: int,
    initial_equity: float,
    returns: list[float],
    block_size: int,
    af: float,
) -> MonteCarloResult:
    if block_size < 1:
        raise ValueError("block_size must be >= 1")
    n = len(returns)
    if n < block_size:
        raise ValueError("block_size larger than returns list")

    final_returns: list[float] = []
    max_drawdowns: list[float] = []
    sharpes: list[float] = []
    target_len = n
    for _ in range(n_paths):
        path: list[float] = []
        while len(path) < target_len:
            start = rng.randint(0, n - block_size)
            path.extend(returns[start:start + block_size])
        path = path[:target_len]
        equity = initial_equity
        peak = equity
        worst_dd = 0.0
        for r in path:
            equity *= (1.0 + r)
            if equity > peak:
                peak = equity
            elif peak > 0:
                dd = (equity / peak - 1.0) * 100.0
                if dd < worst_dd:
                    worst_dd = dd
        final_returns.append((equity / initial_equity - 1.0) * 100.0 if initial_equity > 0 else 0.0)
        max_drawdowns.append(worst_dd)
        mean = statistics.fmean(path)
        std = statistics.pstdev(path)
        sharpes.append((mean / std) * math.sqrt(af) if std > 0 else 0.0)

    return MonteCarloResult(
        n_paths=n_paths,
        final_return_pct=_percentiles(final_returns),
        max_drawdown_pct=_percentiles(max_drawdowns),
        sharpe=_percentiles(sharpes),
    )


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    sorted_v = sorted(values)
    n = len(sorted_v)

    def at(p: float) -> float:
        # Linear interpolation between order statistics.
        idx = p * (n - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return sorted_v[lo]
        frac = idx - lo
        return sorted_v[lo] * (1 - frac) + sorted_v[hi] * frac

    return {
        "min": sorted_v[0],
        "p5": at(0.05),
        "p25": at(0.25),
        "p50": at(0.50),
        "p75": at(0.75),
        "p95": at(0.95),
        "max": sorted_v[-1],
        "mean": statistics.fmean(values),
        "stdev": statistics.pstdev(values),
    }
