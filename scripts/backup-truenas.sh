#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/docker/.env}"
source "$PROJECT_DIR/scripts/lib/env.sh"

if [ ! -f "$ENV_FILE" ]; then
  echo "Arquivo de ambiente não encontrado: $ENV_FILE" >&2
  exit 1
fi

POSTGRES_DB="$(get_env POSTGRES_DB)"
POSTGRES_USER="$(get_env POSTGRES_USER)"
POSTGRES_DB="${POSTGRES_DB:-omnikestr}"
POSTGRES_USER="${POSTGRES_USER:-omnikestr}"
BACKUP_DIR="${BACKUP_DIR:-$(get_env APPS_ROOT)/OmnIkestrAPIM/backups}"
KEEP="${BACKUP_KEEP:-14}"
OUTPUT="${1:-$BACKUP_DIR/omnikestr-$(date +%Y%m%d-%H%M%S).dump}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
TMP="${OUTPUT}.partial"
trap 'rm -f "$TMP"' ERR

cd "$PROJECT_DIR"
compose exec -T db pg_dump -Fc -Z9 -U "$POSTGRES_USER" -d "$POSTGRES_DB" > "$TMP"
compose exec -T db pg_restore -l < "$TMP" >/dev/null
mv -f "$TMP" "$OUTPUT"
trap - ERR
chmod 600 "$OUTPUT"

ls -1t "$BACKUP_DIR"/omnikestr-*.dump 2>/dev/null |
  tail -n +$((KEEP + 1)) |
  xargs -r rm -f --

echo "Backup criado: $OUTPUT"
