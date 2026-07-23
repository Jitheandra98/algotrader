# Security model & threat analysis

Where this bot's safety boundary is, what it defends against, and where it
trusts the operator.

## Threat model

| Adversary                          | Capability                                                     | Mitigation |
|------------------------------------|----------------------------------------------------------------|------------|
| External attacker on the internet  | scans, brute-force, exploits                                  | Operator API behind TLS + auth (operator-side); no public broker creds; rate limits on alerts |
| Malicious insider with cluster ops | edit configs, exec into pods, read secrets                    | Audit trail (every decision logged), secrets in vault not in env files committed to repo, no broker creds stored in plaintext outside the vault |
| Compromised dependency             | malicious package version                                     | Pinned versions in `poetry.lock`; CI fails if lock changes unexpectedly; `pip-audit` / `safety` in CI (manual today, automated in M10 follow-up) |
| Bug in strategy code               | floods orders / sizes too big                                  | **Risk engine is unbypassable** (ADR-0010); per-trade caps; kill switch |
| Bug in execution code              | duplicate orders                                              | Idempotent submit via `client_order_id`; broker enforces it |
| Operator finger-trouble            | accidentally enables live; pushes wrong strategy              | Three-flag gate for live: `LIVE=true` env + per-strategy `live: true` config + allowlist |

## Authentication & authorization

- The Operator API has **no built-in auth** as of M10. **Run it behind an
  authenticated reverse proxy** (nginx with basic-auth, or an OAuth2 proxy).
  Do NOT expose `/api/kill-switch/trip` to the public internet without auth.
- The broker SDK authenticates via API keys held in the secret store.
- The risk engine has no notion of "user" — it acts on signal stream
  origin (strategy_id). Multi-tenant separation is out of scope.

## Secret management

- **Never** commit secrets. `secrets.yaml.example` is the template;
  populated copies live in your secret store (Vault, AWS Secrets Manager,
  GCP Secret Manager, Sealed Secrets).
- `core.secrets.EnvSecretProvider` reads from environment variables.
  Replace with a Vault-backed provider for production:
  ```python
  class VaultSecretProvider(SecretProvider):
      def get(self, key): return vault.read(f"secret/algotrader/{key}").data["value"]
  ```
- Rotate Alpaca keys quarterly. See [rotate_secrets.sh](../deploy/scripts/rotate_secrets.sh).
- Rotate database passwords annually or on any access pattern change.

## Live-trading guardrails (defense in depth)

For real money to move, all three must be true:

  1. `LIVE=true` in the environment / secret.
  2. `broker.live: true` in the global config (per-deployment).
  3. `live: true` on the *specific strategy's* config block.

Plus the strategy must be in `STRATEGIES_LIVE_ALLOWED` (configmap).

Defaults to `false` at every layer. Removing any one of these reverts to
paper trading. The Alpaca broker class refuses to connect to the live URL
without `LIVE=true` set.

## Risk engine integrity

ADR-0010 specifies: every signal-to-order path runs through `RiskEngine`.
The strategy interface has no broker reference; the engine owns the only
broker reference. Bypassing the risk engine requires modifying the engine
code itself.

The risk audit log captures every decision, including allows. M10 wires
this to a write-once table (`risk_decisions`) in Postgres so a bug or
operator action that bypasses risk would still leave a trace.

## Audit trail integrity

- Risk decisions: in-memory ring buffer (M6) → DB (M10 followup).
- Trade journal: JSONL append-only + DB mirror.
- Broker fills: stored in `fills` table; not deleted by any process.

The constraint in the project brief is explicit: *"Never delete persisted
trade history."* No migration script may DROP or TRUNCATE the `fills`,
`risk_decisions`, or `journal_entries` tables. CI lints check for these.

## Network policy

`deploy/k8s/network-policies.yaml` enforces default-deny with explicit
allows:

- `ingester` → Redis, Postgres, Alpaca data API.
- `live-engine` → Redis, Postgres, Alpaca trading API + WS, alert webhooks.
- `operator-api` (in the live-engine pod) → only ingress from the ingress
  controller namespace and the Prometheus namespace.

No pod-to-pod traffic on un-specified ports. Modify carefully.

## Dependency hygiene

- All dependencies pinned via `poetry.lock`.
- Recommended CI additions (TODO in M10 followup):
  - `pip-audit --strict` on each PR.
  - `bandit` for static analysis.
  - Dependabot for upstream CVE notifications.

## Disaster recovery

See [runbooks/disaster-recovery.md](runbooks/disaster-recovery.md). Backups
live in S3 / your blob store with 14-day rotation by default. RTO 4h,
RPO 1 trading day for tick data; zero for positions / fills.

## Known gaps (M10 follow-ups)

- Operator API has no built-in auth — front with a proxy.
- Risk-audit log not yet persisted to DB.
- Dependency scanning not yet automated in CI.
- No request signing on the operator API; rely on the reverse-proxy auth.
- Pod-level seccomp / AppArmor profiles not yet defined.
