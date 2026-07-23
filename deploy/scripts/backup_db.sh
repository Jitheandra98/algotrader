#!/usr/bin/env bash
# Backup the algotrader Postgres + Timescale database.
#
# Strategy:
#   - pg_dump custom format (compact, parallel-restore friendly)
#   - tee through gzip
#   - rotate to ${RETAIN} most recent (default 14)
#   - upload to S3 if AWS_PROFILE / AWS_S3_BUCKET set
#
# Usage:
#   ./backup_db.sh [--db-url postgresql://...] [--out-dir ./backups]
#
# Env overrides:
#   DATABASE_URL, OUT_DIR, RETAIN, AWS_S3_BUCKET

set -euo pipefail

DB_URL="${DATABASE_URL:-postgresql://algotrader:algotrader@localhost:5432/algotrader}"
OUT_DIR="${OUT_DIR:-./backups}"
RETAIN="${RETAIN:-14}"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db-url) DB_URL="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --retain) RETAIN="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$OUT_DIR"
FILE="$OUT_DIR/algotrader-${TIMESTAMP}.dump.gz"

echo "[backup] dumping → $FILE"
pg_dump --format=custom --no-owner --no-privileges "$DB_URL" \
  | gzip -9 > "$FILE"

SIZE="$(du -h "$FILE" | cut -f1)"
echo "[backup] done: $FILE ($SIZE)"

# Rotate
echo "[backup] rotating, keeping last $RETAIN..."
ls -1t "$OUT_DIR"/algotrader-*.dump.gz 2>/dev/null \
  | tail -n +"$((RETAIN + 1))" \
  | xargs -r rm -v

# Optional S3 upload
if [[ -n "${AWS_S3_BUCKET:-}" ]]; then
  echo "[backup] uploading to s3://${AWS_S3_BUCKET}/..."
  aws s3 cp "$FILE" "s3://${AWS_S3_BUCKET}/algotrader/$(basename "$FILE")"
fi

echo "[backup] OK"
