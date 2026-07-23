"""StrategyManager fanout, dedup, and lifecycle tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from core.schemas import AssetClass, Bar, BarTimeframe, Direction, Instrument, Signal
from strategies import (
    Lifecycle,
    Strategy,
    StrategyConfig,
    StrategyManager,
    StrategySlot,
    create,
)

UTC = timezone.utc


def _inst(symbol: str = "SPY") -> Instrument:
    return Instrument(symbol=symbol, asset_class=AssetClass.ETF)


class _StubStrategy(Strategy):
    name = "stub"
    version = "1"

    def __init__(self, config, signal_count=0):
        super().__init__(config)
        self._signal_count = signal_count
        self._calls = 0
        self._configured = False

    def configure(self, ctx):
        self._configured = True

    def on_bar(self, bar, ctx):
        self._calls += 1
        for _ in range(self._signal_count):
            ctx.emit_signal(
                Signal(
                    strategy_id=self.name,
                    instrument=bar.instrument,
                    direction=Direction.LONG,
                    conviction=0.5,
                    rationale="stub",
                )
            )


def _bar(inst: Instrument, t: int = 0) -> Bar:
    return Bar(
        instrument=inst,
        timeframe=BarTimeframe.M1,
        timestamp=datetime(2026, 5, 25, 13, 30, tzinfo=UTC) + timedelta(minutes=t),
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"), close=Decimal("100.5"),
        volume=Decimal("1000"),
    )


def test_manager_unions_instruments() -> None:
    spy = _inst("SPY")
    qqq = _inst("QQQ")
    child_a = _StubStrategy(StrategyConfig(name="a", instruments=[spy]))
    child_b = _StubStrategy(StrategyConfig(name="b", instruments=[qqq]))
    mgr = StrategyManager(
        StrategyConfig(name="mgr", instruments=[]),
        children=[StrategySlot(child_a), StrategySlot(child_b)],
    )
    assert set(i.key for i in mgr.config.instruments) == {spy.key, qqq.key}


def test_manager_routes_bars_to_subscribed_children_only() -> None:
    spy = _inst("SPY")
    qqq = _inst("QQQ")
    child_a = _StubStrategy(StrategyConfig(name="a", instruments=[spy]))
    child_b = _StubStrategy(StrategyConfig(name="b", instruments=[qqq]))
    mgr = StrategyManager(
        StrategyConfig(name="mgr", instruments=[]),
        children=[StrategySlot(child_a), StrategySlot(child_b)],
    )
    # Use a stub ctx — just record signals.
    class _Ctx:
        def __init__(self):
            self._sig = []

        def emit_signal(self, s):
            self._sig.append(s)

        def request_indicator(self, *a, **k):
            return "k"

        @property
        def now(self):
            return datetime(2026, 5, 25, tzinfo=UTC)

        def indicator(self, key):
            return None

        def position(self, inst):
            return None

        @property
        def cash(self):
            return Decimal("0")

        @property
        def equity(self):
            return Decimal("0")

    ctx = _Ctx()
    mgr.on_bar(_bar(spy), ctx)
    assert child_a._calls == 1
    assert child_b._calls == 0


def test_manager_skips_disabled_children() -> None:
    spy = _inst("SPY")
    child_a = _StubStrategy(StrategyConfig(name="a", instruments=[spy]))
    child_b = _StubStrategy(StrategyConfig(name="b", instruments=[spy]))
    mgr = StrategyManager(
        StrategyConfig(name="mgr", instruments=[]),
        children=[
            StrategySlot(child_a, Lifecycle.PAPER),
            StrategySlot(child_b, Lifecycle.DISABLED),
        ],
    )

    class _Ctx:
        def emit_signal(self, s): pass
        def request_indicator(self, *a, **k): return "k"
        now = datetime(2026, 5, 25, tzinfo=UTC)
        def indicator(self, k): return None
        def position(self, i): return None
        cash = equity = Decimal("0")

    mgr.on_bar(_bar(spy), _Ctx())
    assert child_a._calls == 1
    assert child_b._calls == 0


def test_set_lifecycle_changes_dispatch() -> None:
    spy = _inst("SPY")
    child = _StubStrategy(StrategyConfig(name="x", instruments=[spy]))
    mgr = StrategyManager(
        StrategyConfig(name="mgr", instruments=[]),
        children=[StrategySlot(child)],
    )

    class _Ctx:
        def emit_signal(self, s): pass
        def request_indicator(self, *a, **k): return "k"
        now = datetime(2026, 5, 25, tzinfo=UTC)
        def indicator(self, k): return None
        def position(self, i): return None
        cash = equity = Decimal("0")

    mgr.on_bar(_bar(spy), _Ctx())
    assert child._calls == 1
    assert mgr.set_lifecycle("x", Lifecycle.DISABLED)
    mgr.on_bar(_bar(spy), _Ctx())
    assert child._calls == 1  # unchanged
