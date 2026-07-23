# ADR-0014 — Production hardening boundaries

**Status:** Accepted (M10)
**Date:** 2026-05-25

## Context

Three boundary questions resolved in M10:

  1. Should the live-engine pod be scalable horizontally?
  2. Should the operator API ship with built-in auth?
  3. How do we make "never bypass risk / never delete trade history" enforceable
     in production, not just in code review?

## Decision

- **Live engine is single-replica**. The K8s `live-engine.yaml` hard-codes
  `replicas: 1` with `strategy.type: Recreate`. Multi-replica live trading
  needs distributed kill-switch state and broker reconciliation, which we
  haven't built. The deployment is annotated
  `safety.algotrader.io/replicas-locked: "1"` as a flag for cluster
  policy enforcement.
- **Operator API ships with no built-in auth**. The expectation is
  reverse-proxy auth (nginx basic-auth, OAuth2 proxy, etc). Built-in auth
  would lock the design to one identity system; deferring to a proxy keeps
  it open. The README and security doc make this explicit so the gap isn't
  silently accepted.
- **Risk bypass + audit-log deletion are CI-enforced**. The
  `pyproject.toml` test discipline + a lint rule (M10 followup) flag any
  PR that touches `RiskEngine.decide` to skip limits or any migration that
  contains `DROP TABLE` for fills / risk_decisions / journal_entries.
- **DR is rehearsed quarterly**. Backups exist (`backup_db.sh`,
  `snapshot_artifacts.sh`) but backups you never restore are unverified.
  The `disaster-recovery.md` runbook prescribes a quarterly staging drill.
- **Live trading requires three independent flags**. `LIVE=true` env +
  global broker.live + per-strategy live + K8s allowlist. Defense in
  depth; removing any single flag reverts to paper.

## Consequences

- Multi-account / multi-strategy operators must run multiple isolated
  deployments rather than scaling one. Each deployment is one risk
  envelope. Trade-off: more pods, less complex state.
- The operator UI cannot be deployed naked on the internet. We document
  this prominently.
- A future change that adds horizontal scaling needs to land along with
  distributed kill-switch state, distributed risk-audit log, and broker
  reconciliation. We don't half-build it.

## Out of scope

- Distributed coordination (Raft / etcd) for kill-switch and risk audit.
- Multi-region deployments. The operator picks one region and treats it
  as authoritative.
- Compliance certifications (SOC 2, PCI). Out of scope; document the
  controls in `security.md` and let users / firms add the paperwork.
