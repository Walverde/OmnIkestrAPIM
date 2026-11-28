#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/docker/.env"
OUTPUT="${1:-$PROJECT_DIR/backups/omnikestr-$(date +%Y%m%d-%H%M%S).dump}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Arquivo de ambiente não encontrado: $ENV_FILE" >&2
  exit 1
fi

get_env() {
  sed -n "s/^${1}=//p" "$ENV_FILE" | head -n1
}

POSTGRES_DB="$(get_env POSTGRES_DB)"
POSTGRES_USER="$(get_env POSTGRES_USER)"
POSTGRES_DB="${POSTGRES_DB:-omnikestr}"
POSTGRES_USER="${POSTGRES_USER:-omnikestr}"

mkdir -p "$(dirname "$OUTPUT")"
chmod 700 "$(dirname "$OUTPUT")"
cd "$PROJECT_DIR"
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml \
  exec -T db pg_dump -Fc -Z9 -U "$POSTGRES_USER" -d "$POSTGRES_DB" > "$OUTPUT"
chmod 600 "$OUTPUT"
echo "Backup criado: $OUTPUT"
