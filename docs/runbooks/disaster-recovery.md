# Runbook — Disaster recovery

Worst-case scenarios: cluster lost, region down, database corrupted. This
runbook gets you back to operational state.

## Recovery objectives

| Metric                       | Target |
|------------------------------|--------|
| RTO (recovery time)          | 4 hours |
| RPO (data loss tolerance)    | 1 trading day of ticks; **zero positions/fills** |

Positions and fills are the priority — those are money. Tick history can
be re-fetched from the broker.

## Order of operations

1. **Confirm broker state first.** Log into the broker UI directly. What
   positions exist? What orders are open? This is the source of truth for
   the next 4 hours.

2. **Stand up infrastructure.**
   ```bash
   kubectl create -f deploy/k8s/namespace.yaml
   kubectl create -f deploy/k8s/secrets.yaml         # from secret store
   kubectl create -f deploy/k8s/configmap.yaml
   kubectl apply -f deploy/k8s/postgres.yaml
   kubectl apply -f deploy/k8s/redis.yaml
   ```
   Wait for Postgres + Redis to be Ready.

3. **Restore the database.** Use the most recent backup. For Postgres:
   ```bash
   ./deploy/scripts/restore_db.sh --file backups/algotrader-LATEST.dump.gz
   ```
   For artifacts (model, feature store, journal):
   ```bash
   tar -xzf backups/artifacts/artifacts-LATEST.tar.gz -C ./
   ```

4. **DO NOT start the live-engine yet.** Verify:
   - DB has the expected tables and approximate row counts.
   - Artifacts directory has the model + journal.

5. **Apply the migrations job** to be sure schema is current:
   ```bash
   kubectl apply -f deploy/k8s/migrations-job.yaml
   ```

6. **Start the operator-api only** (NOT the live-engine yet):
   ```bash
   # Set live-engine replicas to 0 temporarily.
   kubectl -n algotrader scale deployment/live-engine --replicas=0
   kubectl -n algotrader scale deployment/ingester --replicas=1
   ```

7. **Verify ingestion is healthy** before going live:
   - Open `/api/health` — `data_feed_gap` should be green within a minute.
   - Check Grafana for tick rate.

8. **Reconcile positions.** Before starting the live engine:
   - Compare positions in DB to positions reported by the broker.
   - If mismatched: trust the broker. Manually update the DB or write a
     reconcile-on-start fix and redeploy.

9. **Trip the kill switch** before starting the live engine:
   ```bash
   curl -X POST $OPERATOR_API/api/kill-switch/trip?reason=DR-startup
   ```

10. **Start the live engine, but with kill switch on:**
    ```bash
    kubectl -n algotrader scale deployment/live-engine --replicas=1
    ```

11. **Watch logs.** Wait for:
    - Account sync log line (`live_account_synced ...`).
    - Indicator warmup complete.
    - At least one bar / tick flow.

12. **Verify on the operator UI:**
    - Positions match the broker.
    - Cash matches.
    - Health green.

13. **Reset the kill switch.** Trading resumes.

## What's tolerable to lose

- The last ~1 trading day of tick history: replay from the broker.
- The last few minutes of in-flight Redis-stream messages: gone if Redis was
  the failure point.
- Real-time metrics history in Prometheus: re-accumulates.

## What is NEVER acceptable to lose

- Closed trade records (journal entries) and their P&L attribution.
- Position records (must reconcile with broker).
- The configuration that produced any historical trades.

The journal is JSONL-on-disk + DB-mirrored (after M10 schema). Test the
backup of both regularly.

## Quarterly drill

Run this runbook in a staging cluster every quarter. Confirm:
- Backup restore time stays under 1 hour
- Schema migrations apply cleanly
- The operator UI loads after restore
- Reconciliation against a paper broker shows no drift
