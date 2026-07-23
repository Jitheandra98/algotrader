#!/usr/bin/env bash
# Snapshot the trade journal, feature store, and ML model artifacts.
# These live outside the DB (JSONL + joblib) and need their own backup.
set -euo pipefail

ARTIFACT_DIR="${ARTIFACT_DIR:-./artifacts}"
OUT_DIR="${OUT_DIR:-./backups/artifacts}"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"

mkdir -p "$OUT_DIR"
ARCHIVE="$OUT_DIR/artifacts-${TIMESTAMP}.tar.gz"

if [[ ! -d "$ARTIFACT_DIR" ]]; then
  echo "[snapshot] no artifacts dir at $ARTIFACT_DIR — nothing to snapshot"
  exit 0
fi

tar -czf "$ARCHIVE" -C "$(dirname "$ARTIFACT_DIR")" "$(basename "$ARTIFACT_DIR")"
echo "[snapshot] → $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

if [[ -n "${AWS_S3_BUCKET:-}" ]]; then
  aws s3 cp "$ARCHIVE" "s3://${AWS_S3_BUCKET}/algotrader/artifacts/$(basename "$ARCHIVE")"
  echo "[snapshot] uploaded to S3"
fi
