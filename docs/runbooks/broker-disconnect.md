# Runbook — Broker disconnect

## Symptom

  - Alert: `[ERROR] broker_connection unhealthy`
  - Operator UI shows `broker_connection: FAIL`
  - Metrics: `algotrader_health_status{check="broker_connection"} == 0`
  - The circuit breaker has likely tripped the kill switch after 3 consecutive
    unhealthy verdicts (default).

## Triage

  1. Confirm the broker side is actually down vs network issue:
     ```bash
     curl -fsS https://paper-api.alpaca.markets/v2/clock
     ```
     If this returns 200, the broker is up and the issue is network / auth.
  2. Check pod logs for the reconnect loop:
     ```bash
     kubectl -n algotrader logs deployment/live-engine --tail=200 | grep alpaca_ws_reconnect
     ```
  3. Check broker status page (Alpaca: status.alpaca.markets).

## Recovery

### If broker is down (their problem)

  1. Leave the kill switch tripped.
  2. Wait for broker to recover (check status page).
  3. The reconnect loop will resume automatically.
  4. Verify `/api/health` returns healthy.
  5. Reset kill switch via the UI or `POST /api/kill-switch/reset`.

### If the issue is auth (key revoked / expired)

  1. Rotate credentials via [rotate-secrets runbook](../deploy/scripts/rotate_secrets.sh).
  2. After rollout, verify `/api/health`.
  3. Reset kill switch.

### If the issue is network policy

  1. Check `network-policies.yaml` allows egress on 443 from `live-engine`.
  2. If outbound DNS is the issue, verify CoreDNS pods are healthy.

## Postmortem prompts

  - How long was the disconnect?
  - Were any orders in flight at the time of disconnect? (Check audit log.)
  - Did the reconnect succeed automatically, or require intervention?
  - Should we increase the reconnect backoff cap?
