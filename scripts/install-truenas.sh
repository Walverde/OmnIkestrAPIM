#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
ACTION="install"

for argument in "$@"; do
  case "$argument" in
    --upgrade) ACTION="upgrade" ;;
    --uninstall) ACTION="uninstall" ;;
    *) echo "Opção desconhecida: $argument" >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 não está disponível." >&2
  exit 1
fi

read_value() {
  local prompt="$1"
  local default="$2"
  local answer=""
  read -r -p "$prompt [$default]: " answer
  printf '%s' "${answer:-$default}"
}

APPS_ROOT="${APPS_ROOT:-$(read_value 'Pool e raiz dos datasets (/mnt/...)' "/mnt/tank")}"
BIND_IP="${BIND_IP:-$(read_value 'IP de bind' "0.0.0.0")}"
PORT="${OMNIKESTRAPIM_PORT:-8000}"
VERSION="${OMNIKESTRAPIM_VERSION:-latest}"
APP_DIR="$APPS_ROOT/OmnIkestrAPIM"
PRODUCT_DIR="$APPS_ROOT/OmnIkestrAPIM-Products"
SECRET_DIR="$APP_DIR/secrets"
ENV_FILE="$PROJECT_DIR/docker/.env"

if [ "$ACTION" = "uninstall" ]; then
  cd "$PROJECT_DIR"
  docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml down
  echo "Serviços removidos. Produtos, banco e configuração foram preservados."
  exit 0
fi

if [ "$ACTION" = "upgrade" ]; then
  if command -v zfs >/dev/null 2>&1; then
    for dataset in "$APP_DIR/postgres" "$PRODUCT_DIR"; do
      if zfs list "$dataset" >/dev/null 2>&1; then
        snapshot="${dataset}@pre-upgrade-$(date +%Y%m%d-%H%M%S)"
        zfs snapshot "$snapshot"
        echo "Snapshot criado: $snapshot"
      fi
    done
  fi
fi

mkdir -p "$APP_DIR/postgres" "$PRODUCT_DIR" "$SECRET_DIR"
chown -R 568:568 "$APP_DIR/postgres" "$PRODUCT_DIR" "$SECRET_DIR"
chmod 0750 "$APP_DIR/postgres" "$APP_DIR/secrets"
chmod 0755 "$PRODUCT_DIR"

umask 077
ensure_secret() {
  local key="$1"
  local file="$2"
  local bytes="$3"
  touch "$ENV_FILE"
  if [ -s "$ENV_FILE" ] && grep -Eq "^${key}=.+$" "$ENV_FILE"; then
    if [ -n "$file" ] && [ ! -s "$file" ]; then
      grep "^${key}=" "$ENV_FILE" | cut -d= -f2- > "$file"
      chmod 0440 "$file"
    fi
    return
  fi
  local value
  value="$(openssl rand -hex "$bytes")"
  printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  if [ -n "$file" ]; then
    printf '%s' "$value" > "$file"
    chmod 0440 "$file"
  fi
}

ensure_secret POSTGRES_PASSWORD "$SECRET_DIR/postgres_password" 32
ensure_secret OMNIKESTRAPIM_API_KEY "" 32
ensure_secret OMNIKESTRAPIM_SESSION_SECRET "" 32
ensure_secret OMNIKESTRAPIM_ADMIN_PASSWORD "" 24

cat >> "$ENV_FILE" <<EOF
APPS_ROOT=$APPS_ROOT
BIND_IP=$BIND_IP
OMNIKESTRAPIM_PORT=$PORT
OMNIKESTRAPIM_VERSION=$VERSION
OMNIKESTRAPIM_COOKIE_SECURE=false
UVICORN_PROXY_HEADERS=true
FORWARDED_ALLOW_IPS=127.0.0.1
EOF
chmod 600 "$ENV_FILE"

cd "$PROJECT_DIR"
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml config --quiet
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml pull
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml up -d

for attempt in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "OmnIkestrAPIM iniciado em http://$BIND_IP:$PORT"
    echo "Senha inicial do administrador: $(grep '^OMNIKESTRAPIM_ADMIN_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
    echo "Guarde a senha e proteja o arquivo $ENV_FILE."
    exit 0
  fi
  sleep 2
done

echo "O backend não ficou saudável dentro do tempo limite." >&2
exit 1
