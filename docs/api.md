# Operator API reference

The FastAPI app exposed by `monitoring.api.create_app()`. Mount behind a
TLS-terminating reverse proxy with auth (see [security.md](security.md)).

## Base URL

  - In-cluster: `http://operator-api.algotrader.svc.cluster.local`
  - Public (per your ingress): `https://operator-api.example.com`

## Endpoints

### `GET /`

Serves the operator HTML UI. Single page; polls the REST endpoints every
2 seconds for refresh.

### `GET /api/health`

Health check rollup.

```json
{
  "healthy": true,
  "checks": {
    "data_feed_gap": {
      "healthy": true,
      "detail": "lag=0.3s (cap 10.0s)",
      "last_checked": "2026-05-25T13:30:00Z",
      "metadata": {"lag_s": 0.3}
    },
    "broker_connection": { "healthy": true, "detail": "connected", ... },
    ...
  }
}
```

### `GET /api/portfolio`

Snapshot of equity / cash / positions value.

```json
{ "equity": 102345.67, "cash": 89012.34, "positions_value": 13333.33 }
```

### `GET /api/positions`

List of open positions (serialized `Position` objects).

```json
[
  {
    "instrument": {"symbol": "SPY", "asset_class": "ETF", "venue": "ARCA", "currency": "USD"},
    "quantity": "100",
    "average_price": "451.32",
    "current_price": "453.10",
    "unrealized_pnl": "178.00",
    "realized_pnl": "0.00",
    "strategy_id": "sma_crossover",
    "opened_at": "2026-05-20T13:30:00Z"
  }
]
```

### `GET /api/signals?limit=N` (default 50)

Recent signals emitted by strategies.

### `GET /api/fills?limit=N` (default 50)

Recent broker fills.

### `GET /api/risk-audit?limit=N` (default 50)

Recent risk decisions — both allows and blocks, with rejection rationale.

```json
[
  {
    "signal": { ... full Signal ... },
    "allowed": false,
    "order": null,
    "rejections": [
      {"limit": "max_position_size", "reason": "...", "metadata": {...}},
      {"limit": "gross_leverage",    "reason": "...", "metadata": {...}}
    ],
    "sizing_meta": {"method": "fixed_fractional", "risk_pct": "0.005", ...}
  }
]
```

### `GET /api/strategies`

Strategies and their current lifecycle state.

### `GET /api/kill-switch`

Current kill switch state plus the list of trip reasons since the last
reset.

```json
{ "tripped": true, "reasons": ["[drawdown] drawdown -7.20% breached cap -7.00%"] }
```

### `POST /api/kill-switch/trip?reason=...`

Manually trip the kill switch. Blocks every new entry until reset.

### `POST /api/kill-switch/reset`

Reset the kill switch. **Only use when you understand why it was tripped.**

### `GET /metrics`

Prometheus scrape endpoint. Returns the standard text exposition format.

### `WS /ws`

WebSocket stream — JSON snapshots once per second:

```json
{
  "ts": "2026-05-25T13:30:00.123Z",
  "portfolio": { ... },
  "positions": [ ... ],
  "kill_switch": { "tripped": false, "reasons": [] }
}
```

## Status codes

| Code | Meaning |
|------|---------|
| 200  | Success |
| 503  | Component not wired (e.g., kill switch endpoints when no switch passed in) |
| 422  | Validation error (FastAPI default) |
