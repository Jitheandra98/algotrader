#!/usr/bin/env bash
# Restore an algotrader Postgres dump.
#
# IMPORTANT: Restoring REPLACES the target database. The script demands
# explicit confirmation and refuses to proceed against a production-shaped
# URL unless --i-know-what-im-doing is passed.
#
# Usage:
#   ./restore_db.sh --file backups/algotrader-XYZ.dump.gz --db-url postgresql://...
#
set -euo pipefail

FILE=""
DB_URL="${DATABASE_URL:-}"
CONFIRM_PROD="no"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file) FILE="$2"; shift 2 ;;
    --db-url) DB_URL="$2"; shift 2 ;;
    --i-know-what-im-doing) CONFIRM_PROD="yes"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$FILE" || -z "$DB_URL" ]]; then
  echo "usage: $0 --file <dump.gz> --db-url <postgres-url>" >&2
  exit 2
fi
if [[ ! -f "$FILE" ]]; then
  echo "[restore] file not found: $FILE" >&2; exit 2
fi

if [[ "$DB_URL" == *"prod"* || "$DB_URL" == *"live"* ]]; then
  if [[ "$CONFIRM_PROD" != "yes" ]]; then
    echo "[restore] db url looks production-shaped. Pass --i-know-what-im-doing to proceed." >&2
    exit 3
  fi
fi

echo "[restore] target db: ${DB_URL}"
echo "[restore] source dump: ${FILE}"
read -r -p "[restore] this REPLACES the target database. type 'restore' to continue: " confirm
[[ "$confirm" == "restore" ]] || { echo "aborted"; exit 1; }

# Decompress then pg_restore.
gunzip -c "$FILE" | pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$DB_URL"
echo "[restore] OK"

# Quick sanity check
psql "$DB_URL" -c "SELECT COUNT(*) AS ticks FROM ticks; SELECT COUNT(*) AS bars FROM bars;" \
  || echo "[restore] sanity check failed — investigate before resuming trading"
