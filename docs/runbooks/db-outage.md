# Runbook — Database outage

## Symptom

  - Live engine logs flood with `asyncpg.PostgresConnectionError`.
  - Bar / tick writes are failing (you'll see them retry on the Redis stream).
  - The kill switch may or may not trip — DB is not on the critical-path
    health-check list by default. If you've configured it, it will.

## Triage

  1. `kubectl -n algotrader get pods | grep postgres` — is the pod Running?
  2. `kubectl -n algotrader exec -it postgres-0 -- psql -U algotrader -c "SELECT 1"`
  3. Check storage: `kubectl -n algotrader describe pvc data-postgres-0`. Is
     disk full?
  4. Check connection pool errors in live-engine logs.

## Recovery

### Pod restart (transient)

  1. Trip the kill switch manually if not already.
  2. `kubectl -n algotrader rollout restart statefulset/postgres`.
  3. Wait for Postgres to come back.
  4. The live-engine and ingester will reconnect via their pool's retry
     logic. Bus consumers will resume from un-acked messages — **no tick
     data is lost** as long as Redis stayed up.
  5. Reset kill switch.

### Disk full

  1. Trip kill switch.
  2. Run the retention job to drop oldest ticks/bars:
     ```sql
     SELECT drop_chunks('ticks', INTERVAL '90 days');
     SELECT drop_chunks('bars',  INTERVAL '2 years');
     ```
  3. Reclaim space:
     ```sql
     VACUUM FULL ticks;
     VACUUM FULL bars;
     ```
  4. Expand the PVC if needed.
  5. Reset kill switch.

### Total data loss (worst case)

  1. Restore the latest backup — see [disaster-recovery.md](disaster-recovery.md).
  2. Replay missing ticks/bars from the broker's historical API if needed.
  3. Reconcile positions against the broker's source of truth before resuming.

## Postmortem prompts

  - How long was the outage?
  - Did the bus buffer everything during the outage, or did we trim?
  - Are backups recent enough for our RPO target?
  - Was disk pressure detected before it became full?
