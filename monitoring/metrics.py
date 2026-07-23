"""Prometheus metrics — counters, gauges, histograms.

Metrics live in a private ``CollectorRegistry`` so multiple components can
register without polluting the global default. The default
:class:`MetricsRegistry` is exposed as ``REGISTRY`` and used by everything that
ships with this codebase.

Naming follows Prometheus conventions: ``algotrader_<noun>_<unit>``. Labels
are bounded (e.g., we don't label by order id — that'd be unbounded cardinality).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid hard import at module load — prometheus_client is optional for tests
    # that don't exercise metrics. The registry below imports it lazily.
    from prometheus_client import CollectorRegistry as PCRegistry  # noqa: F401


def _import_pc():
    """Import prometheus_client lazily. Raises a clear message if missing."""
    try:
        import prometheus_client as pc

        return pc
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "prometheus_client is required for the metrics module. "
            "Install with `poetry install`."
        ) from e


@dataclass(frozen=True, slots=True)
class _MetricNames:
    # Signal / order / fill counters
    signals_emitted = "algotrader_signals_emitted_total"
    orders_submitted = "algotrader_orders_submitted_total"
    orders_rejected = "algotrader_orders_rejected_total"
    fills = "algotrader_fills_total"
    risk_rejections = "algotrader_risk_rejections_total"
    # Portfolio gauges
    equity = "algotrader_equity_usd"
    cash = "algotrader_cash_usd"
    position_count = "algotrader_open_position_count"
    unrealized_pnl = "algotrader_unrealized_pnl_usd"
    realized_pnl_daily = "algotrader_realized_pnl_daily_usd"
    drawdown_pct = "algotrader_drawdown_pct"
    # Execution
    fill_latency = "algotrader_fill_latency_ms"
    slippage_per_unit = "algotrader_slippage_per_unit"
    # Health
    health_status = "algotrader_health_status"   # 1=healthy, 0=unhealthy per check label
    kill_switch_tripped = "algotrader_kill_switch_tripped"  # 1 if tripped
    data_lag_seconds = "algotrader_data_lag_seconds"


N = _MetricNames()


class MetricsRegistry:
    """Thin facade over ``prometheus_client`` with our well-known metric set."""

    def __init__(self, *, namespace: str = "") -> None:
        pc = _import_pc()
        self._pc = pc
        self._registry = pc.CollectorRegistry()
        ns_prefix = f"{namespace}_" if namespace else ""

        # Counters
        self.signals = pc.Counter(
            ns_prefix + N.signals_emitted,
            "Signals emitted by strategies",
            labelnames=("strategy", "instrument", "direction"),
            registry=self._registry,
        )
        self.orders_submitted = pc.Counter(
            ns_prefix + N.orders_submitted,
            "Orders submitted to broker",
            labelnames=("strategy", "side", "type"),
            registry=self._registry,
        )
        self.orders_rejected = pc.Counter(
            ns_prefix + N.orders_rejected,
            "Orders rejected by broker (after risk).",
            labelnames=("strategy", "reason"),
            registry=self._registry,
        )
        self.fills = pc.Counter(
            ns_prefix + N.fills,
            "Broker fills",
            labelnames=("strategy", "instrument", "side"),
            registry=self._registry,
        )
        self.risk_rejections = pc.Counter(
            ns_prefix + N.risk_rejections,
            "Signals rejected by the risk engine",
            labelnames=("strategy", "limit"),
            registry=self._registry,
        )

        # Gauges
        self.equity = pc.Gauge(
            ns_prefix + N.equity, "Account equity (USD)", registry=self._registry
        )
        self.cash = pc.Gauge(
            ns_prefix + N.cash, "Cash balance (USD)", registry=self._registry
        )
        self.position_count = pc.Gauge(
            ns_prefix + N.position_count, "Open positions", registry=self._registry
        )
        self.unrealized_pnl = pc.Gauge(
            ns_prefix + N.unrealized_pnl, "Unrealized P&L (USD)", registry=self._registry
        )
        self.realized_pnl_daily = pc.Gauge(
            ns_prefix + N.realized_pnl_daily,
            "Realized P&L since session start (USD)",
            registry=self._registry,
        )
        self.drawdown_pct = pc.Gauge(
            ns_prefix + N.drawdown_pct,
            "Current drawdown from peak equity (percent, negative)",
            registry=self._registry,
        )
        self.health_status = pc.Gauge(
            ns_prefix + N.health_status,
            "Health check status (1=healthy, 0=unhealthy)",
            labelnames=("check",),
            registry=self._registry,
        )
        self.kill_switch = pc.Gauge(
            ns_prefix + N.kill_switch_tripped,
            "Kill switch state (1=tripped)",
            registry=self._registry,
        )
        self.data_lag = pc.Gauge(
            ns_prefix + N.data_lag_seconds,
            "Seconds since the last tick / bar on each stream",
            labelnames=("stream",),
            registry=self._registry,
        )

        # Histograms
        self.fill_latency = pc.Histogram(
            ns_prefix + N.fill_latency,
            "Submit-to-fill latency (ms)",
            buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
            labelnames=("broker",),
            registry=self._registry,
        )
        self.slippage = pc.Histogram(
            ns_prefix + N.slippage_per_unit,
            "Slippage per unit (signed; positive = adverse)",
            buckets=(-0.10, -0.05, -0.01, 0, 0.01, 0.05, 0.10, 0.25, 0.50, 1.00),
            labelnames=("strategy",),
            registry=self._registry,
        )

    @property
    def registry(self):
        return self._registry

    # ---- helpers — short methods that hide the metric names from callers ----

    def on_signal_emitted(self, strategy: str, instrument: str, direction: str) -> None:
        self.signals.labels(strategy, instrument, direction).inc()

    def on_order_submitted(self, strategy: str, side: str, order_type: str) -> None:
        self.orders_submitted.labels(strategy, side, order_type).inc()

    def on_fill(self, strategy: str, instrument: str, side: str) -> None:
        self.fills.labels(strategy, instrument, side).inc()

    def on_risk_rejection(self, strategy: str, limit: str) -> None:
        self.risk_rejections.labels(strategy, limit).inc()

    def observe_fill_latency(self, ms: float, broker: str) -> None:
        self.fill_latency.labels(broker).observe(ms)

    def observe_slippage(self, value: float, strategy: str) -> None:
        self.slippage.labels(strategy).observe(value)

    def set_portfolio(
        self,
        *,
        equity: float,
        cash: float,
        position_count: int,
        unrealized_pnl: float,
        realized_pnl_daily: float,
    ) -> None:
        self.equity.set(equity)
        self.cash.set(cash)
        self.position_count.set(position_count)
        self.unrealized_pnl.set(unrealized_pnl)
        self.realized_pnl_daily.set(realized_pnl_daily)

    def set_drawdown_pct(self, pct: float) -> None:
        self.drawdown_pct.set(pct)

    def set_health(self, check_name: str, healthy: bool) -> None:
        self.health_status.labels(check_name).set(1 if healthy else 0)

    def set_kill_switch(self, tripped: bool) -> None:
        self.kill_switch.set(1 if tripped else 0)

    def set_data_lag(self, stream: str, seconds: float) -> None:
        self.data_lag.labels(stream).set(seconds)

    def export(self) -> bytes:
        """Render the current snapshot in Prometheus text exposition format."""
        return self._pc.generate_latest(self._registry)
