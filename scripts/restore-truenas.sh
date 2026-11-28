#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Uso: $0 <arquivo-backup.dump>" >&2
  exit 2
fi

BACKUP_FILE="$1"
PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/docker/.env"

if [ ! -f "$BACKUP_FILE" ] || [ ! -r "$BACKUP_FILE" ]; then
  echo "Arquivo de backup inválido: $BACKUP_FILE" >&2
  exit 1
fi
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

cd "$PROJECT_DIR"
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml stop backend watcher
SECURITY_BACKUP="${BACKUP_FILE}.pre-restore-$(date +%Y%m%d-%H%M%S).dump"
bash "$PROJECT_DIR/scripts/backup-truenas.sh" "$SECURITY_BACKUP"
pg_restore_output="$(mktemp)"
trap 'rm -f "$pg_restore_output"' EXIT
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml \
  exec -T db pg_restore --clean --if-exists --no-owner --no-privileges \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$BACKUP_FILE"
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml start backend watcher
echo "Restauração concluída. Backup de segurança: $SECURITY_BACKUP"
