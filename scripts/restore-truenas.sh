#!/usr/bin/env bash
set -euo pipefail

YES=false
POSITIONAL=()
for argument in "$@"; do
  if [ "$argument" = "--yes" ]; then
    YES=true
  else
    POSITIONAL+=("$argument")
  fi
done
BACKUP_FILE="${POSITIONAL[0]:-}"
if [ -z "$BACKUP_FILE" ]; then
  echo "Uso: $0 [--yes] <arquivo-backup.dump>" >&2
  exit 2
fi

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/docker/.env"
source "$PROJECT_DIR/scripts/lib/env.sh"

if [ ! -f "$BACKUP_FILE" ] || [ ! -r "$BACKUP_FILE" ]; then
  echo "Arquivo de backup inválido: $BACKUP_FILE" >&2
  exit 1
fi
POSTGRES_DB="$(get_env POSTGRES_DB)"
POSTGRES_USER="$(get_env POSTGRES_USER)"
POSTGRES_DB="${POSTGRES_DB:-omnikestr}"
POSTGRES_USER="${POSTGRES_USER:-omnikestr}"

cd "$PROJECT_DIR"
compose exec -T db pg_restore -l < "$BACKUP_FILE" >/dev/null || {
  echo "Dump inválido ou corrompido." >&2
  exit 1
}

if [ "$YES" != true ]; then
  read -r -p "Restaurar sobrescrevendo os dados atuais? [n/N]: " answer
  [ "${answer:-n}" = "y" ] || [ "${answer:-n}" = "Y" ] || exit 2
fi

compose stop backend watcher
SECURITY_BACKUP="${BACKUP_FILE}.pre-restore-$(date +%Y%m%d-%H%M%S).dump"
bash "$PROJECT_DIR/scripts/backup-truenas.sh" "$SECURITY_BACKUP"

compose exec -T db pg_restore --clean --if-exists --single-transaction --exit-on-error \
  --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$BACKUP_FILE" || {
  echo "Restauração falhou; nenhum conteúdo parcial foi aplicado por transação única." >&2
  echo "Backend e watcher permanecem parados." >&2
  echo "Rollback: bash $PROJECT_DIR/scripts/restore-truenas.sh --yes $SECURITY_BACKUP" >&2
  exit 1
}

compose start backend watcher
echo "Restauração concluída. Backup de segurança: $SECURITY_BACKUP"
