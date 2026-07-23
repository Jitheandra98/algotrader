#!/usr/bin/env bash
# Secret rotation playbook (Alpaca + DB password).
#
# Process:
#   1. Generate new credentials.
#   2. Trip the kill switch (no new entries during rotation).
#   3. Update the secret in your secret store.
#   4. Roll the deployment so pods pick up the new secret.
#   5. Verify health.
#   6. Reset the kill switch.
#
# This script orchestrates steps 2/4/5/6. Steps 1/3 depend on your secret store.

set -euo pipefail

NAMESPACE="${NAMESPACE:-algotrader}"
OPERATOR_API="${OPERATOR_API:-http://operator-api.$NAMESPACE.svc.cluster.local}"

confirm() {
  read -r -p "$1 [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; exit 1; }
}

echo "== Secret rotation =="
echo "1) Generate new credentials in your secret store (Vault / AWS Secrets Manager)."
confirm "Done?"

echo "2) Tripping the kill switch to halt new entries..."
curl -fsS -X POST "$OPERATOR_API/api/kill-switch/trip?reason=secret-rotation" >/dev/null
echo "   → tripped."

echo "3) Update the Kubernetes Secret (kubectl apply -f new-secrets.yaml)."
confirm "Done?"

echo "4) Rolling deployments..."
kubectl -n "$NAMESPACE" rollout restart deployment/ingester deployment/live-engine

echo "5) Waiting for rollouts to complete..."
kubectl -n "$NAMESPACE" rollout status deployment/live-engine --timeout=180s
kubectl -n "$NAMESPACE" rollout status deployment/ingester --timeout=180s

echo "6) Verifying health..."
sleep 5
HEALTH="$(curl -fsS "$OPERATOR_API/api/health" | grep -o '"healthy":[a-z]*' | head -1)"
echo "   → $HEALTH"

echo "7) Resetting the kill switch (only if health is good)..."
confirm "Health OK, reset the switch?"
curl -fsS -X POST "$OPERATOR_API/api/kill-switch/reset" >/dev/null

echo "== Rotation complete =="
