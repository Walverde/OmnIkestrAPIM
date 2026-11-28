#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Uso: $0 <arquivo-backup.sql.gz>" >&2
  exit 2
fi

BACKUP_FILE="$1"
PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/docker/.env"

if [ ! -f "$BACKUP_FILE" ] || [ ! -r "$BACKUP_FILE" ]; then
  echo "Arquivo de backup inválido: $BACKUP_FILE" >&2
  exit 1
fi

cd "$PROJECT_DIR"
gzip -t "$BACKUP_FILE"
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml stop backend
gzip -dc "$BACKUP_FILE" |
  docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml \
    exec -T db psql -v ON_ERROR_STOP=1 -U omnikestr -d omnikestr
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml start backend
