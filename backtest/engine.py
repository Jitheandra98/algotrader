"""Backtest engine — drives a single end-to-end run.

Architecture (per bar, in order):

  1. ``broker.match(bar)``      — fill pending orders against bar OHLC
  2. ``portfolio.apply_fill(...)`` for each fill; call ``strategy.on_fill``
  3. Update all registered indicators with the bar
  4. Call ``strategy.on_bar`` — strategy emits signals
  5. Convert signals → orders via Risk (M6) — for M4 we ship a stub
  6. ``broker.submit(order)`` for each new order
  7. ``portfolio.mark_bar(bar)`` — equity-curve append

Multi-instrument backtests interleave bars across instruments in timestamp order.
Engine fans bar events to the strategy only when the instrument is one the
strategy declared in configure().
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from core.logging import get_logger
from core.schemas import (
    Bar,
    Fill,
    Instrument,
    Order,
    OrderSide,
    OrderType,
    Position,
    Signal,
    Direction,
)
from backtest.broker import SimulatedBroker
from backtest.kpis import KPISummary, compute_kpis
from backtest.portfolio import Portfolio, TradeRecord
from features import create as create_indicator
from features.base import Indicator, IndicatorReading
from risk.engine import RiskEngine
from strategies.base import Strategy, StrategyContext
from strategies.ensemble import SignalFilter

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    portfolio: Portfolio
    fills: list[Fill]
    trades: list[TradeRecord]
    kpis: KPISummary
    signals: list[Signal] = field(default_factory=list)
    risk_decisions: list = field(default_factory=list)


# ---------- Risk stub for M4 ----------


@dataclass(frozen=True, slots=True)
class FixedFractionalRiskConfig:
    """For M4 — a minimal risk model. M6 replaces this with the full risk layer."""

    risk_per_trade_pct: Decimal = Decimal("0.01")  # 1% of equity at risk
    target_allocation_pct: Decimal = Decimal("0.10")  # if no stop: 10% of equity
    max_position_pct: Decimal = Decimal("0.20")  # hard cap


def _signal_to_order(
    signal: Signal,
    *,
    cfg: FixedFractionalRiskConfig,
    equity: Decimal,
    current_price: Decimal,
    current_position: Position | None,
    broker_name: str,
) -> Order | None:
    """Convert a Signal to an Order using fixed-fractional sizing.

    Brief: 'Risk management must sit between strategies and order execution — no
    strategy can bypass it.' This is the M4 stub. The full M6 risk layer will
    replace it.
    """
    # Exit → opposite side of current position. HOLD is a no-op signal.
    if signal.direction is Direction.HOLD:
        return None
    if signal.direction is Direction.EXIT:
        if current_position is None or current_position.quantity == 0:
            return None
        side = OrderSide.SELL if current_position.quantity > 0 else OrderSide.BUY
        qty = abs(current_position.quantity)
        return Order(
            client_order_id=f"{signal.strategy_id}-{signal.id.hex[:8]}",
            instrument=signal.instrument,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            strategy_id=signal.strategy_id,
            broker=broker_name,
        )

    # Direction: LONG or SHORT.
    side = OrderSide.BUY if signal.direction is Direction.LONG else OrderSide.SELL
    if signal.stop_price is not None and current_price != signal.stop_price:
        risk_amount = equity * cfg.risk_per_trade_pct
        risk_per_unit = abs(current_price - signal.stop_price)
        if risk_per_unit <= 0:
            return None
        qty = risk_amount / risk_per_unit
    else:
        allocation = equity * cfg.target_allocation_pct
        qty = allocation / current_price if current_price > 0 else Decimal("0")

    # Apply max position cap.
    max_qty_by_cap = (equity * cfg.max_position_pct) / current_price if current_price > 0 else Decimal("0")
    qty = min(qty, max_qty_by_cap)

    # Scale by conviction (0..1).
    qty = qty * Decimal(str(signal.conviction))

    # Floor to a sane minimum and round to 4 decimal places (crypto-friendly).
    qty = qty.quantize(Decimal("0.0001"))
    if qty <= 0:
        return None

    return Order(
        client_order_id=f"{signal.strategy_id}-{signal.id.hex[:8]}",
        instrument=signal.instrument,
        side=side,
        order_type=OrderType.MARKET,
        quantity=qty,
        strategy_id=signal.strategy_id,
        broker=broker_name,
    )


# ---------- BacktestContext ----------


class BacktestContext:
    """Concrete :class:`StrategyContext` for backtests. The engine owns one and
    passes it to the strategy on every call.

    Also implements :class:`risk.base.RiskContext` so the same context object can
    be passed straight to ``RiskEngine.decide``.
    """

    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio
        self._now: datetime | None = None
        self._indicators: dict[str, tuple[Indicator, Instrument]] = {}
        self._last_readings: dict[str, IndicatorReading] = {}
        self._emitted_signals: list[Signal] = []
        # Risk-context state (populated by the engine).
        self._last_bars: dict[str, Bar] = {}
        self._atrs: dict[str, Decimal] = {}
        self._ssr_flags: set[str] = set()
        self._sector_map: dict[str, str] = {}
        self._is_margin_account: bool = True
        # Rolling returns per instrument for correlation calc.
        self._returns: dict[str, list[float]] = {}
        self._correlation_lookback: int = 60

    # ---- protocol implementation ----

    @property
    def now(self) -> datetime:
        if self._now is None:
            raise RuntimeError("context.now accessed before any bar processed")
        return self._now

    def indicator(self, key: str) -> IndicatorReading | None:
        return self._last_readings.get(key)

    def position(self, instrument: Instrument) -> Position | None:
        return self._portfolio.position(instrument)

    @property
    def cash(self) -> Decimal:
        return self._portfolio.cash

    @property
    def equity(self) -> Decimal:
        return self._portfolio.equity()

    def emit_signal(self, signal: Signal) -> None:
        self._emitted_signals.append(signal)

    def request_indicator(
        self,
        instrument: Instrument,
        indicator_name: str,
        *,
        key: str | None = None,
        **params: Any,
    ) -> str:
        """Register an indicator. If the same (instrument, name, params) was
        already requested, returns the existing key — the indicator is shared.
        This lets multiple strategies request RSI(14) on SPY without paying for
        two parallel computations."""
        if key is None:
            key = self._make_key(instrument, indicator_name, params)
        if key in self._indicators:
            return key
        ind = create_indicator(indicator_name, **params)
        self._indicators[key] = (ind, instrument)
        return key

    # ---- engine-side helpers ----

    @staticmethod
    def _make_key(instrument: Instrument, name: str, params: dict[str, Any]) -> str:
        param_str = ",".join(f"{k}={v}" for k, v in sorted(params.items()))
        return f"{instrument.key}:{name}({param_str})"

    def _set_now(self, ts: datetime) -> None:
        self._now = ts

    def _drain_signals(self) -> list[Signal]:
        out = list(self._emitted_signals)
        self._emitted_signals.clear()
        return out

    def _update_indicators_for(self, bar: Bar) -> None:
        for key, (ind, inst) in self._indicators.items():
            if inst.key != bar.instrument.key:
                continue
            self._last_readings[key] = ind.update(bar)

    # ---- RiskContext protocol implementation ----

    @property
    def account_value(self) -> Decimal:
        return self._portfolio.equity()

    @property
    def is_margin_account(self) -> bool:
        return self._is_margin_account

    def positions(self) -> list[Position]:
        return self._portfolio.positions

    def last_bar(self, instrument: Instrument) -> Bar | None:
        return self._last_bars.get(instrument.key)

    def atr(self, instrument: Instrument) -> Decimal | None:
        return self._atrs.get(instrument.key)

    def recent_trade_pnls(self, n: int) -> list[Decimal]:
        trades = self._portfolio.trades
        return [t.pnl for t in trades[-n:]]

    def realized_pnl_since(self, since: datetime) -> Decimal:
        total = Decimal("0")
        for t in self._portfolio.trades:
            if t.exit_time >= since:
                total += t.pnl
        return total

    def equity_at(self, ts: datetime) -> Decimal | None:
        # Find first equity point at or after ts.
        for pt in self._portfolio.equity_curve:
            if pt.timestamp >= ts:
                return pt.equity
        return None

    def is_ssr_flagged(self, instrument: Instrument) -> bool:
        return instrument.key in self._ssr_flags

    def sector_of(self, instrument: Instrument) -> str | None:
        return self._sector_map.get(instrument.symbol) or self._sector_map.get(instrument.key)

    def correlation(self, a: Instrument, b: Instrument) -> float | None:
        ra = self._returns.get(a.key, [])
        rb = self._returns.get(b.key, [])
        n = min(len(ra), len(rb))
        if n < self._correlation_lookback:
            return None
        xa = ra[-n:]
        xb = rb[-n:]
        ma = sum(xa) / n
        mb = sum(xb) / n
        num = sum((xa[i] - ma) * (xb[i] - mb) for i in range(n))
        da = (sum((x - ma) ** 2 for x in xa)) ** 0.5
        db = (sum((x - mb) ** 2 for x in xb)) ** 0.5
        if da == 0 or db == 0:
            return None
        return num / (da * db)

    # ---- engine-side state updates ----

    def _record_bar(self, bar: Bar) -> None:
        """Maintain last-bar cache and rolling returns for correlation."""
        prev = self._last_bars.get(bar.instrument.key)
        self._last_bars[bar.instrument.key] = bar
        if prev is not None and prev.close > 0:
            ret = float(bar.close / prev.close - 1)
            buf = self._returns.setdefault(bar.instrument.key, [])
            buf.append(ret)
            if len(buf) > self._correlation_lookback * 2:
                self._returns[bar.instrument.key] = buf[-self._correlation_lookback * 2:]

    def _set_atr(self, instrument: Instrument, atr: Decimal) -> None:
        self._atrs[instrument.key] = atr

    def set_sector_map(self, mapping: dict[str, str]) -> None:
        self._sector_map.update(mapping)

    def set_margin_account(self, is_margin: bool) -> None:
        self._is_margin_account = is_margin

    def set_ssr_flag(self, instrument: Instrument, flagged: bool) -> None:
        if flagged:
            self._ssr_flags.add(instrument.key)
        else:
            self._ssr_flags.discard(instrument.key)


# ---------- Engine ----------


class BacktestEngine:
    """One-shot backtest runner. Use :meth:`process_bar` per bar and
    :meth:`finalize` to compute the KPI summary."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        broker: SimulatedBroker,
        portfolio: Portfolio,
        risk: FixedFractionalRiskConfig | None = None,
        risk_engine: RiskEngine | None = None,
        signal_filters: list[SignalFilter] | None = None,
    ) -> None:
        self._strategy = strategy
        self._broker = broker
        self._portfolio = portfolio
        self._risk = risk or FixedFractionalRiskConfig()
        # If a full RiskEngine is provided, it replaces the M4 stub path.
        # Otherwise we fall back to the fixed-fractional stub for backward compat
        # with M4-era backtests.
        self._risk_engine = risk_engine
        self._ctx = BacktestContext(portfolio)
        self._signal_filters: list[SignalFilter] = list(signal_filters or [])
        self._configured = False
        self._all_fills: list[Fill] = []
        self._all_signals: list[Signal] = []
        self._risk_decisions: list = []
        self._bars_with_position = 0
        self._subscribed_keys: set[str] = set()

    @property
    def context(self) -> BacktestContext:
        return self._ctx

    def configure(self) -> None:
        """Run the strategy's one-time setup. Idempotent — second call no-ops."""
        if self._configured:
            return
        self._strategy.configure(self._ctx)
        for inst in self._strategy.config.instruments:
            self._subscribed_keys.add(inst.key)
        self._configured = True

    def process_bar(self, bar: Bar) -> None:
        if not self._configured:
            self.configure()
        self._ctx._set_now(bar.timestamp)
        # Risk-context bookkeeping: cache last bar + returns history.
        self._ctx._record_bar(bar)

        # 1) Match pending orders against this bar (orders submitted on the
        # *previous* bar fill now — broker enforces no look-ahead).
        fills = self._broker.match(bar)
        for fill in fills:
            self._all_fills.append(fill)
            self._portfolio.apply_fill(fill, strategy_id=self._strategy.name)
            if self._risk_engine is not None:
                self._risk_engine.on_fill(fill, self._ctx)
            if bar.instrument.key in self._subscribed_keys:
                self._strategy.on_fill(fill, self._ctx)

        # 2) Update indicators for this bar.
        self._ctx._update_indicators_for(bar)

        # 3) Hand the bar to the strategy if it's subscribed.
        if bar.instrument.key in self._subscribed_keys:
            self._strategy.on_bar(bar, self._ctx)

        # 4) Drain emitted signals, run them through the filter chain, convert
        # to orders via the risk engine, submit.
        signals = self._ctx._drain_signals()
        for f in self._signal_filters:
            signals = f.filter(signals, now=bar.timestamp)
        self._all_signals.extend(signals)
        for signal in signals:
            if self._risk_engine is not None:
                decision = self._risk_engine.decide(signal, self._ctx)
                self._risk_decisions.append(decision)
                if decision.allowed and decision.order is not None:
                    self._broker.submit(decision.order)
            else:
                current_pos = self._portfolio.position(signal.instrument)
                order = _signal_to_order(
                    signal,
                    cfg=self._risk,
                    equity=self._portfolio.equity(),
                    current_price=bar.close,
                    current_position=current_pos,
                    broker_name=self._broker.name,
                )
                if order is not None:
                    self._broker.submit(order)

        # 5) Mark-to-market and append equity point.
        self._portfolio.mark_bar(bar)
        if self._portfolio.open_position_count > 0:
            self._bars_with_position += 1

    def finalize(self) -> BacktestResult:
        kpis = compute_kpis(
            self._portfolio.equity_curve,
            self._portfolio.trades,
            initial_equity=self._portfolio.initial_cash,
            bars_with_position=self._bars_with_position,
        )
        return BacktestResult(
            portfolio=self._portfolio,
            fills=list(self._all_fills),
            trades=list(self._portfolio.trades),
            kpis=kpis,
            signals=list(self._all_signals),
            risk_decisions=list(self._risk_decisions),
        )

    def run(self, bars: Iterable[Bar]) -> BacktestResult:
        """Convenience: process all bars then finalize."""
        for bar in bars:
            self.process_bar(bar)
        return self.finalize()
