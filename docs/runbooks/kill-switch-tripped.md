# Runbook — Kill switch tripped

## Symptom

  - Alert: `[CRITICAL] kill_switch_tripped`
  - Operator UI: `kill-switch: TRIPPED` (with the trip reason)
  - Metrics: `algotrader_kill_switch_tripped == 1`
  - Risk engine is rejecting all new entries with the kill switch as the
    reason.

## Triage

The kill switch's `reasons` field lists *all* trips since the last reset.
Read them in order — the FIRST one is usually the root cause; subsequent
ones may be downstream consequences.

```bash
curl -fsS $OPERATOR_API/api/kill-switch
```

Common reasons:

  - `[drawdown] ...` → see [drawdown-breach](drawdown-breach.md)
  - `[health] N consecutive unhealthy: <check_names>` → see the relevant
    health runbook
  - `[latency] fill latency p95 ...` → broker connection or routing issue
  - `[consecutive_loss_kill] ...` → strategy is broken; investigate before
    resuming
  - `operator manual ...` → someone tripped it deliberately; check who and why

## Recovery

**Never reset without understanding the trip reason.** The switch exists to
protect capital.

1. Diagnose using the appropriate sub-runbook above.
2. Fix the root cause (deploy fix, restart pod, wait for upstream recovery).
3. Verify health is green: `curl $OPERATOR_API/api/health`.
4. Verify no open positions are at risk (check `/api/positions` for stops
   in place).
5. Reset the switch:
   ```
   POST /api/kill-switch/reset
   ```
6. Watch the next ~10 risk decisions carefully. If anything looks off,
   re-trip immediately.

## Postmortem prompts

  - Was the trip reason valid (real risk) or a false positive (flaky check)?
  - If valid: did we lose money before the switch fired? How much?
  - If false positive: tune the threshold. Aggressive tuning is OK; we'd
    rather trip too often than too rarely.
