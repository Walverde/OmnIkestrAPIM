#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="$PROJECT_DIR/docker/.env"
ACTION="install"
YES=false

for argument in "$@"; do
  case "$argument" in
    --upgrade) ACTION="upgrade" ;;
    --uninstall) ACTION="uninstall" ;;
    --yes) YES=true ;;
    *) echo "Opção desconhecida: $argument" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Execute este script como root no TrueNAS." >&2
  exit 1
fi
if [ "$ACTION" = "uninstall" ]; then
  if [ -f "$ENV_FILE" ]; then
    cd "$PROJECT_DIR"
    docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml down
  fi
  echo "Serviços removidos. Produtos, banco e configuração foram preservados."
  exit 0
fi
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 não está disponível." >&2
  exit 1
fi
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

read_value() {
  local prompt="$1"
  local default="$2"
  local answer=""
  if [ "$YES" = true ] || [ ! -t 0 ]; then
    printf '%s' "$default"
    return
  fi
  read -r -p "$prompt [$default]: " answer
  printf '%s' "${answer:-$default}"
}

APPS_ROOT="${APPS_ROOT:-$(read_value 'Pool e raiz dos datasets (/mnt/...)' "/mnt/tank")}"
BIND_IP="${BIND_IP:-$(read_value 'IP de bind' "0.0.0.0")}"
PORT="${OMNIKESTRAPIM_PORT:-8000}"
VERSION="${OMNIKESTRAPIM_VERSION:-latest}"
FORWARD_PROXY="${FORWARDED_ALLOW_IPS:-127.0.0.1}"

if [ ! -d "$APPS_ROOT" ]; then
  echo "APPS_ROOT não existe: $APPS_ROOT" >&2
  exit 1
fi

set_env() {
  local key="$1"
  local value="$2"
  touch "$ENV_FILE"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

mkdir -p "$PROJECT_DIR/docker"
chmod 700 "$(dirname "$ENV_FILE")" 2>/dev/null || true
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

APP_DIR="$APPS_ROOT/OmnIkestrAPIM"
PRODUCT_DIR="$APPS_ROOT/OmnIkestrAPIM-Products"
SECRET_DIR="$APP_DIR/secrets"

if command -v zfs >/dev/null 2>&1; then
  if zfs list "$APP_DIR" >/dev/null 2>&1 || zfs list -H -o name "$APP_DIR" >/dev/null 2>&1; then
    :
  fi
fi
mkdir -p "$APP_DIR/postgres" "$PRODUCT_DIR" "$SECRET_DIR"

if [ "$ACTION" = "upgrade" ] && command -v zfs >/dev/null 2>&1; then
  for dataset_path in "$APP_DIR/postgres" "$PRODUCT_DIR"; do
    dataset="$(zfs list -H -o name "$dataset_path" 2>/dev/null)" || {
      echo "Caminho não é um mountpoint ZFS: $dataset_path" >&2
      exit 1
    }
    zfs snapshot "${dataset}@pre-upgrade-$(date +%Y%m%d-%H%M%S)" || exit 1
  done
fi

chmod 0750 "$APP_DIR/postgres" "$SECRET_DIR"
chmod 0755 "$PRODUCT_DIR"

ensure_secret() {
  local key="$1"
  local file="$2"
  local bytes="$3"
  local value=""
  if grep -q "^${key}=.+$" "$ENV_FILE"; then
    value="$(sed -n "s/^${key}=//p" "$ENV_FILE" | head -n1)"
  else
    value="$(openssl rand -hex "$bytes")"
    set_env "$key" "$value"
  fi
  if [ -n "$file" ]; then
    if [ ! -s "$file" ]; then
      printf '%s' "$value" > "$file"
    fi
    chmod 0440 "$file"
    chown 568:568 "$file"
  fi
}

ensure_secret POSTGRES_PASSWORD "$SECRET_DIR/postgres_password" 32
ensure_secret OMNIKESTRAPIM_API_KEY "" 32
ensure_secret OMNIKESTRAPIM_SESSION_SECRET "" 32
ensure_secret OMNIKESTRAPIM_ADMIN_PASSWORD "" 24

set_env APPS_ROOT "$APPS_ROOT"
set_env BIND_IP "$BIND_IP"
set_env OMNIKESTRAPIM_PORT "$PORT"
set_env OMNIKESTRAPIM_VERSION "$VERSION"
set_env OMNIKESTRAPIM_COOKIE_SECURE "${OMNIKESTRAPIM_COOKIE_SECURE:-false}"
set_env UVICORN_PROXY_HEADERS true
set_env FORWARDED_ALLOW_IPS "$FORWARD_PROXY"
chmod 600 "$ENV_FILE"

cd "$PROJECT_DIR"
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml config --quiet
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml pull
docker compose --env-file "$ENV_FILE" -f docker/docker-compose.truenas.yml up -d

HEALTH_HOST="$([ "$BIND_IP" = "0.0.0.0" ] && echo "127.0.0.1" || echo "$BIND_IP")"
for attempt in $(seq 1 60); do
  health="$(curl -fsS "http://$HEALTH_HOST:$PORT/api/health" 2>/dev/null)" || true
  if [ -n "$health" ]; then
    echo "OmnIkestrAPIM iniciado em http://$HEALTH_HOST:$PORT"
    echo "$health"
    exit 0
  fi
  sleep 2
done

docker logs --tail 100 omnikestr-backend-1 2>/dev/null || true
echo "O backend não respondeu em 120 segundos." >&2
exit 1
