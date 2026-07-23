# Configuration reference

All runtime knobs. Layered YAML in `config/` + environment overrides.

## Layering (low → high precedence)

  1. `config/defaults.yaml` — checked-in baseline.
  2. `config/<env>.yaml` — env-specific overrides (`dev`, `staging`, `prod`).
     Selected via `ALGOTRADER_ENV=staging`.
  3. Environment variables — final say.

## Core (`core/config.py`)

| Key                       | Default                          | Description |
|---------------------------|----------------------------------|-------------|
| `LIVE`                    | `false`                          | Master gate for real-money trading. Three-flag rule: this + broker.live + strategy.live must all be true. |
| `ALGOTRADER_ENV`          | `dev`                            | Selects which `<env>.yaml` to merge over defaults |
| `DATABASE_URL`            | `postgresql://algotrader:algotrader@localhost:5432/algotrader` | Postgres / TimescaleDB DSN |
| `REDIS_URL`               | `redis://localhost:6379/0`       | Redis Streams URL |
| `LOG_LEVEL`               | `INFO`                           | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_FORMAT`              | `console`                        | `console` (dev) or `json` (prod) |

## Brokers

```yaml
broker:
  name: alpaca           # or "ibkr", "simulated"
  live: false            # paper unless explicitly true (combined with LIVE=true)
  paper_url: https://paper-api.alpaca.markets
  live_url:  https://api.alpaca.markets
  request_timeout_s: 15
```

## Risk (`risk/`)

```yaml
risk:
  sizer:
    type: fixed_fractional         # or vol_targeted, kelly
    risk_per_trade_pct: 0.005      # 0.5% per brief
    target_alloc_pct: 0.10
  limits:
    - type: per_trade_risk_cap
      max_pct: 0.01
    - type: max_position_size
      max_pct: 0.20
    - type: per_strategy_allocation
      default_pct: 0.20
      allocations:
        sma_crossover: 0.15
    - type: gross_leverage
      max_leverage: 1.0
    - type: net_leverage
      max_long: 1.0
      max_short: 1.0
    - type: daily_drawdown_limit
      max_pct: 0.03
    - type: weekly_drawdown_limit
      max_pct: 0.07
    - type: consecutive_loss_kill
      max_consecutive: 5
    - type: pattern_day_trader
      threshold_account_value: 25000
    - type: short_sale_restriction
    - type: sector_exposure_limit
      default_pct: 0.30
      sector_pct:
        TECH: 0.40
    - type: correlation_limit
      max_cluster_pct: 0.40
      rho_threshold: 0.6
    # Greeks only matters when options chains are wired (M2/M7 followup).
    - type: aggregate_greeks_bound
      max_abs_delta: 100
    - type: wash_sale_guard
      block: false                # flag by default; set true to enforce
```

## Strategies

Per-strategy config blocks. Engine looks them up by `name`.

```yaml
strategies:
  - name: sma_crossover
    enabled: true
    live: false                    # per-strategy live gate (third of three)
    instruments:
      - {symbol: SPY, asset_class: ETF, venue: ARCA}
    parameters:
      fast_period: 20
      slow_period: 50

  - name: rsi_mean_reversion
    enabled: true
    live: false
    instruments:
      - {symbol: QQQ, asset_class: ETF}
    parameters:
      rsi_period: 14
      oversold: 30
      overbought: 70
      trend_period: 200
      atr_period: 14
      stop_atr_mult: 2.0
      allow_short: false
```

## Signal filters (M5)

```yaml
filters:
  - type: confirmation
    min_votes: 2
    window_minutes: 5
  - type: min_conviction
    threshold: 0.6
  - type: stop_opposing
  # Optional ML filter (M9):
  - type: ml_signal_quality
    model_path: artifacts/model.joblib
    threshold: 0.55
```

## Execution / router (M7)

```yaml
execution:
  router:
    slice_above_qty: 1000
    max_spread_bps: 20
    on_wide_spread: limit_at_mid    # or "skip"
    max_pct_of_top: 0.5
  slicer:
    type: twap                       # or simple_split, iceberg
    n: 6
    interval_seconds: 10
```

## Monitoring (M8)

```yaml
monitoring:
  metrics:
    port: 8000                       # served by operator-api
  health:
    data_feed_max_lag_s: 10
    fill_latency_max_p95_ms: 2000
    order_rejection_max_rate: 0.10
    drawdown_warn_pct: 5.0
  circuit_breakers:
    health_trip_after: 3
    drawdown_max_pct: 7.0
    latency_max_p95_ms: 3000
    latency_trip_after: 3
alerts:
  channels:
    slack:
      webhook_url: ${SLACK_WEBHOOK_URL}
    discord:
      webhook_url: ${DISCORD_WEBHOOK_URL}
    email:
      smtp_host: ${SMTP_HOST}
      smtp_port: 587
      from_addr: ${SMTP_FROM}
      to_addrs: [${SMTP_TO}]
  rules:
    - name: critical-everywhere
      min_severity: CRITICAL
      channels: [slack, discord, email]
    - name: errors-slack-only
      min_severity: ERROR
      channels: [slack]
```

## ML (M9)

```yaml
ml:
  enabled: false
  feature_store_path: artifacts/features.jsonl
  model_path: artifacts/model.joblib
  trainer:
    is_size: 500
    oos_size: 100
    anchored: true
```

## Live-trading gate summary

For real money to move, **all** of these must be set:

  1. Environment: `LIVE=true`
  2. Config: `broker.live: true`
  3. Config: `strategies[<name>].live: true`
  4. (K8s) ConfigMap: `STRATEGIES_LIVE_ALLOWED` contains the strategy name

Removing any one falls back to paper. This is defense in depth.
