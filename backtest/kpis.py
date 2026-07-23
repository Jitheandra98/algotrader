"""KPIs — the full suite from the brief.

Inputs: equity curve + trade ledger. Outputs: :class:`KPISummary` containing
return metrics, risk-adjusted metrics, drawdown stats, trade stats, and
exposure.

Conventions:
  - Returns are computed bar-to-bar from the equity curve.
  - Annualization factor is inferred from the median bar spacing (daily → 252,
    hourly → 6.5*252, etc.) unless overridden.
  - Risk-free rate default is 0.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from backtest.portfolio import EquityPoint, TradeRecord


@dataclass(frozen=True, slots=True)
class KPISummary:
    # Equity / return
    initial_equity: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    # Risk-adjusted
    sharpe: float
    sortino: float
    calmar: float
    mar: float
    # Drawdown
    max_drawdown_pct: float
    avg_drawdown_pct: float
    max_drawdown_duration_bars: int
    recovery_factor: float
    # Trade stats
    trades: int
    wins: int
    losses: int
    win_rate_pct: float
    profit_factor: float
    expectancy: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_holding_bars: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    # Exposure
    exposure_pct: float
    # Meta
    bars: int
    years: float
    annualization_factor: float

    def to_dict(self) -> dict[str, float | int]:
        return {f.name: getattr(self, f.name) for f in self.__dataclass_fields__.values()}


def compute_kpis(
    equity_curve: list[EquityPoint],
    trades: list[TradeRecord],
    *,
    initial_equity: Decimal | None = None,
    risk_free_rate: float = 0.0,
    annualization_factor: float | None = None,
    bars_with_position: int | None = None,
) -> KPISummary:
    """Compute the full KPI suite.

    Args:
        equity_curve: chronological equity points.
        trades: closed trades from :class:`Portfolio.trades`.
        initial_equity: override starting equity; default is the first point's.
        risk_free_rate: annualized RFR (decimal, e.g. 0.045). Default 0.
        annualization_factor: bars per year. Inferred from timestamp spacing if None.
        bars_with_position: total bars where at least one position was open.
            Required for exposure %; if None, treated as 0 (caller should pass it).
    """
    if not equity_curve:
        return _empty_kpis()

    initial = float(initial_equity if initial_equity is not None else equity_curve[0].equity)
    final = float(equity_curve[-1].equity)
    total_return_pct = (final / initial - 1.0) * 100.0 if initial > 0 else 0.0

    af = annualization_factor or _infer_annualization(equity_curve)
    years = (equity_curve[-1].timestamp - equity_curve[0].timestamp).total_seconds() / (365.25 * 86400)

    cagr = ((final / initial) ** (1.0 / years) - 1.0) * 100.0 if years > 0 and initial > 0 else 0.0

    returns = _bar_returns(equity_curve)
    sharpe = _sharpe(returns, af, risk_free_rate)
    sortino = _sortino(returns, af, risk_free_rate)
    max_dd_pct, avg_dd_pct, max_dd_dur = _drawdowns(equity_curve)
    recovery = abs((final - initial) / (initial * max_dd_pct / 100.0)) if max_dd_pct < 0 else 0.0

    calmar = (cagr / abs(max_dd_pct)) if max_dd_pct != 0 else 0.0
    mar = calmar  # synonymous in the common convention used here

    trade_stats = _trade_stats(trades)
    exposure_pct = (
        bars_with_position / len(equity_curve) * 100.0
        if bars_with_position is not None and equity_curve
        else 0.0
    )

    return KPISummary(
        initial_equity=initial,
        final_equity=final,
        total_return_pct=total_return_pct,
        cagr_pct=cagr,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        mar=mar,
        max_drawdown_pct=max_dd_pct,
        avg_drawdown_pct=avg_dd_pct,
        max_drawdown_duration_bars=max_dd_dur,
        recovery_factor=recovery,
        trades=trade_stats["count"],
        wins=trade_stats["wins"],
        losses=trade_stats["losses"],
        win_rate_pct=trade_stats["win_rate"],
        profit_factor=trade_stats["profit_factor"],
        expectancy=trade_stats["expectancy"],
        avg_win=trade_stats["avg_win"],
        avg_loss=trade_stats["avg_loss"],
        largest_win=trade_stats["largest_win"],
        largest_loss=trade_stats["largest_loss"],
        avg_holding_bars=trade_stats["avg_holding"],
        max_consecutive_wins=trade_stats["max_cons_wins"],
        max_consecutive_losses=trade_stats["max_cons_losses"],
        exposure_pct=exposure_pct,
        bars=len(equity_curve),
        years=years,
        annualization_factor=af,
    )


def _empty_kpis() -> KPISummary:
    return KPISummary(
        initial_equity=0,
        final_equity=0,
        total_return_pct=0,
        cagr_pct=0,
        sharpe=0,
        sortino=0,
        calmar=0,
        mar=0,
        max_drawdown_pct=0,
        avg_drawdown_pct=0,
        max_drawdown_duration_bars=0,
        recovery_factor=0,
        trades=0,
        wins=0,
        losses=0,
        win_rate_pct=0,
        profit_factor=0,
        expectancy=0,
        avg_win=0,
        avg_loss=0,
        largest_win=0,
        largest_loss=0,
        avg_holding_bars=0,
        max_consecutive_wins=0,
        max_consecutive_losses=0,
        exposure_pct=0,
        bars=0,
        years=0,
        annualization_factor=0,
    )


def _bar_returns(curve: list[EquityPoint]) -> list[float]:
    out: list[float] = []
    for prev, curr in zip(curve, curve[1:]):
        if prev.equity > 0:
            out.append(float(curr.equity / prev.equity - 1))
        else:
            out.append(0.0)
    return out


def _sharpe(returns: list[float], af: float, rfr: float) -> float:
    if len(returns) < 2:
        return 0.0
    mean = statistics.fmean(returns)
    std = statistics.pstdev(returns)
    if std == 0:
        return 0.0
    excess = mean - rfr / af
    return (excess / std) * math.sqrt(af)


def _sortino(returns: list[float], af: float, rfr: float) -> float:
    if len(returns) < 2:
        return 0.0
    mean = statistics.fmean(returns)
    downside = [r for r in returns if r < 0]
    if not downside:
        return float("inf") if mean > 0 else 0.0
    dstd = math.sqrt(sum(r * r for r in downside) / len(returns))
    if dstd == 0:
        return 0.0
    excess = mean - rfr / af
    return (excess / dstd) * math.sqrt(af)


def _drawdowns(curve: list[EquityPoint]) -> tuple[float, float, int]:
    """Return (max_dd_pct, avg_dd_pct, max_dd_duration_bars). All as negative %."""
    if not curve:
        return 0.0, 0.0, 0
    peak = float(curve[0].equity)
    max_dd = 0.0
    drawdowns: list[float] = []
    in_dd = False
    dd_start = 0
    max_duration = 0
    for i, pt in enumerate(curve):
        eq = float(pt.equity)
        if eq >= peak:
            if in_dd:
                drawdowns.append(max_dd)
                max_duration = max(max_duration, i - dd_start)
                in_dd = False
            peak = eq
            continue
        dd = (eq / peak - 1.0) * 100.0 if peak > 0 else 0.0
        if not in_dd:
            in_dd = True
            dd_start = i
            max_dd = dd
        else:
            max_dd = min(max_dd, dd)
    if in_dd:
        drawdowns.append(max_dd)
        max_duration = max(max_duration, len(curve) - dd_start)
    overall_max = min(drawdowns) if drawdowns else 0.0
    overall_avg = statistics.fmean(drawdowns) if drawdowns else 0.0
    return overall_max, overall_avg, max_duration


def _trade_stats(trades: list[TradeRecord]) -> dict[str, float | int]:
    if not trades:
        return {
            "count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "avg_holding": 0.0,
            "max_cons_wins": 0,
            "max_cons_losses": 0,
        }
    pnls = [float(t.pnl) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    win_rate = len(wins) / len(trades) * 100.0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0.0)
    expectancy = statistics.fmean(pnls)
    avg_win = statistics.fmean(wins) if wins else 0.0
    avg_loss = statistics.fmean(losses) if losses else 0.0

    # Holding period in seconds → divide by median bar spacing later if we want
    # "bars" — for simplicity we report it as the raw timedelta in seconds /
    # 86400 (days). Strategies can interpret per their own bar size.
    holdings = [(t.exit_time - t.entry_time).total_seconds() / 86400 for t in trades]
    avg_holding = statistics.fmean(holdings) if holdings else 0.0

    # Streaks
    cur_w = cur_l = max_w = max_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
            max_w = max(max_w, cur_w)
        elif p < 0:
            cur_l += 1
            cur_w = 0
            max_l = max(max_l, cur_l)
        else:
            cur_w = cur_l = 0
    return {
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "largest_win": max(pnls) if pnls else 0.0,
        "largest_loss": min(pnls) if pnls else 0.0,
        "avg_holding": avg_holding,
        "max_cons_wins": max_w,
        "max_cons_losses": max_l,
    }


def _infer_annualization(curve: list[EquityPoint]) -> float:
    """Pick a sensible annualization factor from median bar spacing."""
    if len(curve) < 2:
        return 252.0
    deltas = [
        (b.timestamp - a.timestamp).total_seconds()
        for a, b in zip(curve, curve[1:])
        if (b.timestamp - a.timestamp).total_seconds() > 0
    ]
    if not deltas:
        return 252.0
    median = statistics.median(deltas)
    # Coarse buckets: pick the closest standard bar.
    candidates = {
        timedelta(seconds=60).total_seconds(): 252 * 6.5 * 60,   # 1m intraday
        timedelta(seconds=300).total_seconds(): 252 * 6.5 * 12,  # 5m intraday
        timedelta(seconds=3600).total_seconds(): 252 * 6.5,      # 1h intraday
        timedelta(days=1).total_seconds(): 252.0,                # daily
        timedelta(days=7).total_seconds(): 52.0,                 # weekly
    }
    return min(candidates.items(), key=lambda kv: abs(median - kv[0]))[1]
