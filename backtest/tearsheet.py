"""Tear-sheet generator — renders a backtest result as a markdown document.

Bundles KPIs, trade summary stats, drawdown highlights, and monthly returns. Used
by tests (assert the file shape) and by the operator (eyeball a run's quality
before approving for paper).

Optionally, when ``matplotlib`` is importable, renders an equity-curve PNG and
embeds it. Markdown is canonical — the PNG is a convenience.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any

from backtest.kpis import KPISummary
from backtest.portfolio import EquityPoint, TradeRecord


def render_tearsheet(
    *,
    title: str,
    kpis: KPISummary,
    equity_curve: list[EquityPoint],
    trades: list[TradeRecord],
    monte_carlo: dict[str, Any] | None = None,
    notes: str | None = None,
    output_path: Path | None = None,
    embed_chart_path: Path | None = None,
) -> str:
    """Return the markdown body. If ``output_path`` is given, also writes the file
    (and a PNG of the equity curve next to it when matplotlib is available)."""
    buf = StringIO()
    p = buf.write

    p(f"# {title}\n\n")
    p(f"_generated_: {datetime.utcnow().isoformat(timespec='seconds')}Z\n\n")

    # Summary
    p("## Summary\n\n")
    p("| Metric | Value |\n|---|---|\n")
    p(f"| Initial equity | {kpis.initial_equity:,.2f} |\n")
    p(f"| Final equity | {kpis.final_equity:,.2f} |\n")
    p(f"| Total return | {kpis.total_return_pct:.2f}% |\n")
    p(f"| CAGR | {kpis.cagr_pct:.2f}% |\n")
    p(f"| Years | {kpis.years:.2f} |\n")
    p(f"| Bars | {kpis.bars} |\n")
    p("\n")

    # Risk-adjusted
    p("## Risk-Adjusted\n\n")
    p("| Metric | Value |\n|---|---|\n")
    p(f"| Sharpe | {kpis.sharpe:.2f} |\n")
    p(f"| Sortino | {kpis.sortino:.2f} |\n")
    p(f"| Calmar | {kpis.calmar:.2f} |\n")
    p(f"| MAR | {kpis.mar:.2f} |\n")
    p(f"| Recovery factor | {kpis.recovery_factor:.2f} |\n")
    p("\n")

    # Drawdown
    p("## Drawdown\n\n")
    p("| Metric | Value |\n|---|---|\n")
    p(f"| Max drawdown | {kpis.max_drawdown_pct:.2f}% |\n")
    p(f"| Avg drawdown | {kpis.avg_drawdown_pct:.2f}% |\n")
    p(f"| Max DD duration (bars) | {kpis.max_drawdown_duration_bars} |\n")
    p("\n")

    # Trade stats
    p("## Trade Statistics\n\n")
    p("| Metric | Value |\n|---|---|\n")
    p(f"| Trades | {kpis.trades} |\n")
    p(f"| Wins | {kpis.wins} ({kpis.win_rate_pct:.1f}%) |\n")
    p(f"| Losses | {kpis.losses} |\n")
    p(f"| Profit factor | {kpis.profit_factor:.2f} |\n")
    p(f"| Expectancy | {kpis.expectancy:,.2f} |\n")
    p(f"| Avg win | {kpis.avg_win:,.2f} |\n")
    p(f"| Avg loss | {kpis.avg_loss:,.2f} |\n")
    p(f"| Largest win | {kpis.largest_win:,.2f} |\n")
    p(f"| Largest loss | {kpis.largest_loss:,.2f} |\n")
    p(f"| Max consec. wins | {kpis.max_consecutive_wins} |\n")
    p(f"| Max consec. losses | {kpis.max_consecutive_losses} |\n")
    p(f"| Avg holding (days) | {kpis.avg_holding_bars:.2f} |\n")
    p(f"| Exposure | {kpis.exposure_pct:.1f}% |\n")
    p("\n")

    # Monthly returns
    if equity_curve:
        p("## Monthly Returns\n\n")
        monthly = _monthly_returns(equity_curve)
        if monthly:
            p("| Month | Return |\n|---|---|\n")
            for ym, ret in monthly:
                p(f"| {ym} | {ret:.2f}% |\n")
            p("\n")

    # Monte Carlo
    if monte_carlo:
        p("## Monte Carlo\n\n")
        for label, percentiles in monte_carlo.items():
            p(f"### {label}\n\n")
            if isinstance(percentiles, dict):
                p("| Percentile | Value |\n|---|---|\n")
                for k in ("min", "p5", "p25", "p50", "p75", "p95", "max", "mean", "stdev"):
                    if k in percentiles:
                        p(f"| {k} | {percentiles[k]:.2f} |\n")
                p("\n")

    if notes:
        p("## Notes\n\n")
        p(notes + "\n\n")

    # Optional equity curve chart
    png_path = _maybe_render_chart(equity_curve, embed_chart_path) if equity_curve else None
    if png_path is not None:
        p(f"## Equity Curve\n\n![equity]({png_path.name})\n\n")

    out = buf.getvalue()
    if output_path is not None:
        output_path.write_text(out, encoding="utf-8")
    return out


def _monthly_returns(curve: list[EquityPoint]) -> list[tuple[str, float]]:
    monthly_equity: dict[str, float] = {}
    for p in curve:
        key = p.timestamp.strftime("%Y-%m")
        monthly_equity[key] = float(p.equity)
    keys = sorted(monthly_equity)
    if len(keys) < 2:
        return []
    out: list[tuple[str, float]] = []
    for prev_k, curr_k in zip(keys, keys[1:]):
        prev_v = monthly_equity[prev_k]
        curr_v = monthly_equity[curr_k]
        if prev_v > 0:
            out.append((curr_k, (curr_v / prev_v - 1.0) * 100.0))
    return out


def _maybe_render_chart(curve: list[EquityPoint], path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]
    except ImportError:
        return None
    times = [p.timestamp for p in curve]
    equity = [float(p.equity) for p in curve]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(times, equity)
    ax.set_title("Equity curve")
    ax.set_xlabel("Time")
    ax.set_ylabel("Equity")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path
