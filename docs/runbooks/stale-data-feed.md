# Runbook — Stale data feed

## Symptom

  - Alert: `[ERROR] data_feed_gap unhealthy`
  - Metrics: `algotrader_data_lag_seconds > max_lag` for the affected stream
  - Health check: `data_feed_gap: FAIL`
  - After ~30s of stale data, the circuit breaker trips the kill switch.

## Triage

  1. Check the affected stream — is it the data source for ticks, bars, or
     both?
  2. Check `kubectl -n algotrader logs deployment/ingester --tail=100`. Look
     for `alpaca_ws_reconnect` lines.
  3. Verify Alpaca data API status: `curl -fsS https://data.alpaca.markets/v2/clock`
  4. Verify Redis is healthy: `kubectl -n algotrader exec -it redis-0 -- redis-cli PING`

## Recovery

### If the ingester died

  1. The kill switch already tripped (good).
  2. Restart the ingester deployment:
     ```bash
     kubectl -n algotrader rollout restart deployment/ingester
     ```
  3. Wait ~30s for fresh ticks to flow.
  4. Verify `/api/health` is green.
  5. Reset kill switch.

### If Alpaca side is degraded

  1. Confirm via Alpaca status page.
  2. Leave the kill switch on.
  3. Wait for upstream recovery.
  4. After 5 minutes of clean data, reset kill switch.

### If Redis is unhealthy

  1. Check Redis pod (memory pressure, OOM, disk full).
  2. Recover Redis first; ingestion will resume on its own.
  3. The Redis stream has a max-len trim policy — if it overflowed, some
     historical ticks are gone. Bars persisted to Postgres are still safe.

## Postmortem prompts

  - Was the gap detected within the configured threshold?
  - Were any signals "blind" — emitted on stale data before detection?
  - Should the threshold be tighter for this instrument's typical tick rate?
