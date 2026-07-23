"""Live trading engine.

Mirrors :class:`backtest.engine.BacktestEngine` but driven by async streams
instead of a synchronous bar loop. Wires:

  - :class:`data.base.DataAdapter` (M2)            — bars / ticks in
  - :class:`features` indicators                   — same code as backtest
  - :class:`strategies.Strategy`                   — same code as backtest
  - :class:`risk.RiskEngine`                       — same code as backtest
  - :class:`execution.router.SmartOrderRouter`     — live-only
  - :class:`execution.base.BrokerAdapter`          — live or paper broker

The same strategy code that runs in backtest runs here unchanged. Only the
orchestration (sync bar loop vs async stream) differs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core.bus import EventBus, Stream
from core.logging import get_logger
from core.schemas import (
    Bar,
    BarTimeframe,
    Fill,
    Instrument,
    Position,
    Signal,
    Tick,
)
from data.base import DataAdapter
from execution.base import BrokerAdapter, ExecutionEvent
from execution.quality import FillQualityTracker, reference_price_for
from execution.router import MarketView, SmartOrderRouter
from features import create as create_indicator
from features.base import Indicator, IndicatorReading
from risk.engine import RiskEngine
from strategies.base import Strategy
from strategies.ensemble import SignalFilter

log = get_logger(__name__)


class LiveContext:
    """Implements both StrategyContext and RiskContext for live trading.

    Largely mirrors :class:`backtest.engine.BacktestContext` but the portfolio
    is the live-trading record (we sync with the broker on startup and on
    reconnect; intra-day truth is the local portfolio plus fills as they
    arrive).
    """

    def __init__(self, portfolio_state, market_view: MarketView) -> None:
        self._portfolio = portfolio_state
        self._view = market_view
        self._now: datetime = datetime.now(tz=timezone.utc)
        self._indicators: dict[str, tuple[Indicator, Instrument]] = {}
        self._readings: dict[str, IndicatorReading] = {}
        self._emitted: list[Signal] = []
        self._last_bars: dict[str, Bar] = {}
        self._atrs: dict[str, Decimal] = {}
        self._ssr_flags: set[str] = set()
        self._sector_map: dict[str, str] = {}
        self._is_margin_account: bool = True
        self._account_value: Decimal = Decimal("0")
        self._equity_history: list[tuple[datetime, Decimal]] = []
        self._trade_pnls: list[Decimal] = []

    # ---- StrategyContext ----

    @property
    def now(self) -> datetime:
        return self._now

    def indicator(self, key: str) -> IndicatorReading | None:
        return self._readings.get(key)

    def position(self, instrument: Instrument) -> Position | None:
        return self._portfolio.position(instrument)

    @property
    def cash(self) -> Decimal:
        return self._portfolio.cash

    @property
    def equity(self) -> Decimal:
        return self._portfolio.equity()

    def emit_signal(self, signal: Signal) -> None:
        self._emitted.append(signal)

    def request_indicator(
        self,
        instrument: Instrument,
        indicator_name: str,
        *,
        key: str | None = None,
        **params: Any,
    ) -> str:
        if key is None:
            param_str = ",".join(f"{k}={v}" for k, v in sorted(params.items()))
            key = f"{instrument.key}:{indicator_name}({param_str})"
        if key in self._indicators:
            return key
        ind = create_indicator(indicator_name, **params)
        self._indicators[key] = (ind, instrument)
        return key

    # ---- RiskContext ----

    @property
    def account_value(self) -> Decimal:
        return self._account_value or self.equity

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
        return list(self._trade_pnls[-n:])

    def realized_pnl_since(self, since: datetime) -> Decimal:
        return Decimal("0")  # TODO: hook to portfolio trade ledger

    def equity_at(self, ts: datetime) -> Decimal | None:
        for s_ts, s_eq in reversed(self._equity_history):
            if s_ts <= ts:
                return s_eq
        return None

    def is_ssr_flagged(self, instrument: Instrument) -> bool:
        return instrument.key in self._ssr_flags

    def sector_of(self, instrument: Instrument) -> str | None:
        return self._sector_map.get(instrument.symbol) or self._sector_map.get(instrument.key)

    def correlation(self, a: Instrument, b: Instrument) -> float | None:
        return None  # M9: rolling cross-instrument correlation

    # ---- engine hooks ----

    def _set_now(self, ts: datetime) -> None:
        self._now = ts

    def _drain_signals(self) -> list[Signal]:
        out = list(self._emitted)
        self._emitted.clear()
        return out

    def _update_indicators_for(self, bar: Bar) -> None:
        for key, (ind, inst) in self._indicators.items():
            if inst.key != bar.instrument.key:
                continue
            self._readings[key] = ind.update(bar)
        self._last_bars[bar.instrument.key] = bar


class LiveEngine:
    """Run a strategy live (paper or real money). Background tasks:

      - **bar/tick loop**: consume data adapter → update indicators → run
        strategy → route signals through risk + SOR.
      - **order updates loop**: consume broker stream → publish fills to bus,
        update portfolio, notify strategy on_fill.
    """

    def __init__(
        self,
        *,
        data_adapter: DataAdapter,
        broker: BrokerAdapter,
        strategy: Strategy,
        risk_engine: RiskEngine,
        portfolio,                                    # backtest.portfolio.Portfolio
        bus: EventBus | None = None,
        router: SmartOrderRouter | None = None,
        signal_filters: Sequence[SignalFilter] = (),
        quality_tracker: FillQualityTracker | None = None,
        timeframes: Sequence[BarTimeframe] = (BarTimeframe.M1,),
    ) -> None:
        self._adapter = data_adapter
        self._broker = broker
        self._strategy = strategy
        self._risk = risk_engine
        self._portfolio = portfolio
        self._bus = bus
        market_view = router.market_view if router else MarketView()
        self._router = router or SmartOrderRouter(broker, market_view=market_view)
        self._view = market_view
        self._filters: list[SignalFilter] = list(signal_filters)
        self._quality = quality_tracker or FillQualityTracker()
        self._timeframes = list(timeframes)
        self._ctx = LiveContext(portfolio, self._view)
        self._stop = asyncio.Event()
        self._order_map: dict[str, Any] = {}  # broker_order_id → internal Order

    @property
    def context(self) -> LiveContext:
        return self._ctx

    async def run(self, instruments: Sequence[Instrument]) -> None:
        await self._adapter.connect()
        await self._broker.connect()
        # One-time strategy configuration.
        self._strategy.configure(self._ctx)
        # Sync account snapshot.
        await self._sync_account()

        tasks = [
            asyncio.create_task(self._bar_loop(instruments), name="bar_loop"),
            asyncio.create_task(self._order_loop(), name="order_loop"),
            asyncio.create_task(self._tick_loop(instruments), name="tick_loop"),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        finally:
            self._stop.set()
            for t in tasks:
                t.cancel()
            for t in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await t
            await self._broker.close()
            await self._adapter.close()

    def stop(self) -> None:
        self._stop.set()

    # ---- background loops ----

    async def _bar_loop(self, instruments: Sequence[Instrument]) -> None:
        # Live bars come either from the broker's native bar stream (Alpaca 1m)
        # or from the local TimeBarAggregator on the tick stream. We let the
        # adapter decide.
        async for bar in self._adapter.stream_bars(list(instruments), self._timeframes[0]):
            if self._stop.is_set():
                break
            await self._on_bar(bar)

    async def _tick_loop(self, instruments: Sequence[Instrument]) -> None:
        async for tick in self._adapter.stream_ticks(list(instruments)):
            if self._stop.is_set():
                break
            self._update_quote_from_tick(tick)
            if self._bus is not None:
                # Optional: also publish to the bus so other consumers can subscribe.
                from data.ingestion import tick_to_payload
                await self._bus.publish(Stream.TICKS, tick_to_payload(tick))

    async def _order_loop(self) -> None:
        async for report in self._broker.stream_order_updates():
            if self._stop.is_set():
                break
            await self._handle_report(report)

    # ---- per-event handlers ----

    async def _on_bar(self, bar: Bar) -> None:
        self._ctx._set_now(bar.timestamp)
        self._ctx._update_indicators_for(bar)
        self._strategy.on_bar(bar, self._ctx)
        signals = self._ctx._drain_signals()
        for f in self._filters:
            signals = f.filter(signals, now=bar.timestamp)
        for signal in signals:
            decision = self._risk.decide(signal, self._ctx)
            if decision.allowed and decision.order is not None:
                ref_price = self._reference_price_for(decision.order)
                self._quality.on_submit(decision.order, ref_price)
                routing = await self._router.route(decision.order, now=bar.timestamp)
                for child in routing.children:
                    if child.broker_order_id:
                        self._order_map[child.broker_order_id] = child
            else:
                log.info(
                    "live_signal_rejected",
                    signal_id=str(signal.id),
                    rejections=[r.limit for r in decision.rejections],
                )
        # mark-to-market with the bar's close.
        self._portfolio.mark_bar(bar)

    async def _handle_report(self, report) -> None:
        log.debug("execution_report", event=report.event.value, broker_order_id=report.broker_order_id)
        if report.event in (ExecutionEvent.ACK, ExecutionEvent.NEW):
            if report.broker_order_id:
                self._quality.on_ack(report.order_id, report.timestamp)
        elif report.event in (ExecutionEvent.PARTIAL_FILL, ExecutionEvent.FILL) and report.fill is not None:
            self._portfolio.apply_fill(report.fill, strategy_id=self._strategy.name)
            self._quality.on_fill(report.fill, self._strategy.name, venue=self._broker.name)
            self._risk.on_fill(report.fill, self._ctx)
            self._strategy.on_fill(report.fill, self._ctx)
            if self._bus is not None:
                from data.ingestion import bar_to_payload  # not actually a fill payload — see TODO
                # TODO(M8): proper Stream.FILLS publication
                pass

    def _update_quote_from_tick(self, tick: Tick) -> None:
        self._view.update_quote(
            tick.instrument,
            bid=tick.bid,
            ask=tick.ask,
            bid_size=tick.bid_size,
            ask_size=tick.ask_size,
        )

    def _reference_price_for(self, order) -> Decimal | None:
        q = self._view.get(order.instrument)
        return reference_price_for(
            order,
            bid=q.bid if q else None,
            ask=q.ask if q else None,
            mid=q.mid if q else None,
        )

    async def _sync_account(self) -> None:
        info = await self._broker.account()
        self._ctx._account_value = info.account_value if hasattr(info, "account_value") else info.equity  # type: ignore[attr-defined]
        self._ctx._is_margin_account = info.is_margin_account
        log.info(
            "live_account_synced",
            equity=str(info.equity),
            buying_power=str(info.buying_power),
            margin=info.is_margin_account,
        )
