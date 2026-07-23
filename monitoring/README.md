# `monitoring/` — Observability + alerts + operator UI

What you get:

  - **Prometheus metrics** for the running bot (counters, gauges, histograms).
  - **Health checks** that observe data feed gaps, broker connection, latency,
    rejection rate, drawdown.
  - **Circuit breakers** that auto-trip the risk kill-switch on degraded
    health or DD breach.
  - **Alert dispatcher** routing to Slack, Discord, email, Twilio SMS — rule-based,
    deduplicated, channel failures isolated.
  - **FastAPI operator API** with REST + WebSocket + a minimal dark-mode HTML UI.
  - **Grafana dashboard JSON** (overview, per-strategy, risk).

## Wiring

```python
from monitoring import MetricsRegistry, HealthMonitor, CircuitBreakerSet, ...
from monitoring.api import OperatorState, create_app
from risk import KillSwitch

ks = KillSwitch()
metrics = MetricsRegistry()
monitor = HealthMonitor([
    DataFeedGapCheck(get_last_tick_ts=lambda: my_engine.last_tick_ts, max_lag=timedelta(seconds=10)),
    BrokerConnectionCheck(is_connected=lambda: broker.is_connected),
    LatencyCheck(get_p95_ms=lambda: quality.stats().p95_fill_latency_ms, max_p95_ms=2000),
    OrderRejectionRateCheck(get_submitted=lambda: count_submitted(), get_rejected=lambda: count_rejected()),
    DrawdownCheck(get_drawdown_pct=lambda: current_dd_pct(), warn_pct=5.0),
])
breakers = CircuitBreakerSet([
    HealthCircuitBreaker(kill_switch=ks, monitor=monitor, trip_after=3),
    DrawdownCircuitBreaker(kill_switch=ks, max_pct=7.0, get_drawdown_pct=lambda: current_dd_pct()),
    LatencyCircuitBreaker(kill_switch=ks, get_p95_ms=lambda: quality.stats().p95_fill_latency_ms, max_p95_ms=3000),
])

# Async loop somewhere: breakers.evaluate_all() every N seconds.

state = OperatorState(
    portfolio_provider=lambda: {"equity": float(pf.equity()), "cash": float(pf.cash)},
    positions_provider=lambda: pf.positions,
    signals_provider=lambda n: recent_signals[-n:],
    fills_provider=lambda n: recent_fills[-n:],
    risk_audit_provider=lambda n: risk_engine.audit_log[-n:],
    kill_switch=ks,
    health_monitor=monitor,
    metrics=metrics,
)
app = create_app(state)
# uvicorn app:app --port 8000
```

## Endpoints

| Method | Path                         | Purpose |
|--------|------------------------------|---------|
| GET    | `/`                          | dark-mode operator UI (HTML+JS) |
| GET    | `/api/health`                | per-check verdicts + overall flag |
| GET    | `/api/portfolio`             | equity/cash/positions_value snapshot |
| GET    | `/api/positions`             | open positions (Pydantic-serialized) |
| GET    | `/api/signals?limit=N`       | recent signals |
| GET    | `/api/fills?limit=N`         | recent fills |
| GET    | `/api/risk-audit?limit=N`    | recent risk decisions (with rejections) |
| GET    | `/api/strategies`            | registered strategies + lifecycle |
| GET    | `/api/kill-switch`           | current trip state + reasons |
| POST   | `/api/kill-switch/trip`      | manually trip with `?reason=...` |
| POST   | `/api/kill-switch/reset`     | reset |
| GET    | `/metrics`                   | Prometheus scrape endpoint |
| WS     | `/ws`                        | live snapshot every 1s |

## Metrics (Prometheus)

| Name                                     | Type      | Labels |
|------------------------------------------|-----------|--------|
| `algotrader_signals_emitted_total`       | counter   | strategy, instrument, direction |
| `algotrader_orders_submitted_total`      | counter   | strategy, side, type |
| `algotrader_orders_rejected_total`       | counter   | strategy, reason |
| `algotrader_fills_total`                 | counter   | strategy, instrument, side |
| `algotrader_risk_rejections_total`       | counter   | strategy, limit |
| `algotrader_equity_usd`                  | gauge     | — |
| `algotrader_cash_usd`                    | gauge     | — |
| `algotrader_open_position_count`         | gauge     | — |
| `algotrader_unrealized_pnl_usd`          | gauge     | — |
| `algotrader_realized_pnl_daily_usd`      | gauge     | — |
| `algotrader_drawdown_pct`                | gauge     | — |
| `algotrader_kill_switch_tripped`         | gauge     | — |
| `algotrader_health_status`               | gauge     | check |
| `algotrader_data_lag_seconds`            | gauge     | stream |
| `algotrader_fill_latency_ms`             | histogram | broker |
| `algotrader_slippage_per_unit`           | histogram | strategy |

## Grafana dashboards

Pre-built JSON in `monitoring/dashboards/` — import directly into Grafana:

  - `overview.json` — equity, DD, signal / fill rates, latency, health.
  - `strategy.json` — per-strategy filterable view (signal direction, rejections).
  - `risk.json` — kill-switch, DD, rejections by limit/reason, feed lag.

## Alert channels

| Channel  | Setup |
|----------|-------|
| Slack    | webhook URL in config |
| Discord  | webhook URL in config |
| Email    | SMTP host/port + user/pass + from + to[] |
| Twilio   | account SID + auth token + from + to[] (paid; reserve for CRITICAL) |

Channel failures don't block other channels. Same-title alerts within a
configurable window are deduplicated.

## Circuit breakers

| Breaker        | Trigger |
|----------------|---------|
| Health         | N consecutive unhealthy verdicts |
| Drawdown       | DD ≥ cap (one-shot) |
| Latency        | p95 > cap for N consecutive evaluations |

All breakers trip the same `KillSwitch` — once tripped, the risk engine blocks
every new order until an operator reset.
