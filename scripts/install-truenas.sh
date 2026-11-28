#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="$PROJECT_DIR/docker/.env"
ACTION="install"
YES=false
SKIP_SNAPSHOT=false
ENABLE_MARKETPLACE=false
PROXY_IP=""

for argument in "$@"; do
  case "$argument" in
    --upgrade) ACTION="upgrade" ;;
    --uninstall) ACTION="uninstall" ;;
    --yes) YES=true ;;
    --skip-snapshot) SKIP_SNAPSHOT=true ;;
    --with-marketplace) ENABLE_MARKETPLACE=true ;;
    --proxy-ip) shift_placeholder="${1:-}" ;;
    --proxy-ip=*) PROXY_IP="${argument#*=}" ;;
    *) echo "Opção desconhecida: $argument" >&2; exit 2 ;;
  esac
done

for ((i=1; i<=$#; i++)); do
  if [ "${!i}" = "--proxy-ip" ] && (( i + 1 <= $# )); then
    j=$((i+1))
    PROXY_IP="${!j}"
  fi
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Execute este script como root no TrueNAS." >&2
  exit 1
fi
mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
source "$SCRIPT_DIR/lib/env.sh"

if [ "$ACTION" = "uninstall" ]; then
  compose down
  echo "Serviços removidos. Produtos, banco e configuração foram preservados."
  exit 0
fi

if [ -f "$ENV_FILE" ]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 não está disponível." >&2
  exit 1
fi

read_value() {
  local prompt="$1"
  local default="$2"
  local answer=""
  if [ "$YES" = true ] || [ ! -t 0 ]; then
    printf '%s' "$default"
  else
    read -r -p "$prompt [$default]: " answer
    printf '%s' "${answer:-$default}"
  fi
}

APPS_ROOT="${APPS_ROOT:-$(read_value 'Pool e raiz dos datasets (/mnt/...)' '/mnt/tank')}"
BIND_IP="${BIND_IP:-$(read_value 'IP de bind' '0.0.0.0')}"
PORT="${OMNIKESTRAPIM_PORT:-8000}"
VERSION="${OMNIKESTRAPIM_VERSION:-latest}"
FORWARD_PROXY="${PROXY_IP:-${FORWARDED_ALLOW_IPS:-127.0.0.1}}"

if [[ ! "$APPS_ROOT" =~ ^/mnt/[A-Za-z0-9._/-]+$ ]]; then
  echo "APPS_ROOT inválido: $APPS_ROOT" >&2
  exit 1
fi
if [ ! -d "$APPS_ROOT" ]; then
  echo "APPS_ROOT não existe: $APPS_ROOT" >&2
  exit 1
fi

set_env APPS_ROOT "$APPS_ROOT"
set_env BIND_IP "$BIND_IP"
set_env OMNIKESTRAPIM_PORT "$PORT"
set_env OMNIKESTRAPIM_VERSION "$VERSION"
set_env OMNIKESTRAPIM_COOKIE_SECURE "${OMNIKESTRAPIM_COOKIE_SECURE:-false}"
set_env UVICORN_PROXY_HEADERS true
set_env FORWARDED_ALLOW_IPS "$FORWARD_PROXY"
set_env ENABLE_MARKETPLACE "$([ "$ENABLE_MARKETPLACE" = true ] && echo true || echo "${ENABLE_MARKETPLACE:-false}")"

ROOT_DS=""
if command -v zfs >/dev/null 2>&1; then
  ROOT_DS="$(zfs list -H -o name "$APPS_ROOT" 2>/dev/null)" || ROOT_DS=""
fi
if [ -n "$ROOT_DS" ]; then
  zfs create -p -o recordsize=16K "$ROOT_DS/OmnIkestrAPIM/postgres" 2>/dev/null || true
  zfs create -p "$ROOT_DS/OmnIkestrAPIM-Products" 2>/dev/null || true
  APP_DIR="$APPS_ROOT/OmnIkestrAPIM"
  PRODUCT_DIR="$APPS_ROOT/OmnIkestrAPIM-Products"
else
  echo "AVISO: APPS_ROOT não é um mountpoint ZFS; snapshots ZFS ficarão indisponíveis." >&2
  APP_DIR="$APPS_ROOT/OmnIkestrAPIM"
  PRODUCT_DIR="$APPS_ROOT/OmnIkestrAPIM-Products"
fi
PRODUCT_CREATED=false
if [ ! -d "$PRODUCT_DIR" ]; then
  PRODUCT_CREATED=true
fi
mkdir -p "$APP_DIR/postgres" "$PRODUCT_DIR" "$APP_DIR/secrets"
chmod 0750 "$APP_DIR/postgres" "$APP_DIR/secrets"
if [ "$PRODUCT_CREATED" = true ]; then
  chown 568:568 "$PRODUCT_DIR"
fi
if command -v setpriv >/dev/null 2>&1; then
  if ! setpriv --reuid=568 --regid=568 --clear-groups test -w "$PRODUCT_DIR"; then
    echo "AVISO: UID 568 não grava em $PRODUCT_DIR; ajuste a ACL do dataset." >&2
  fi
fi

GENERATED_KEYS=""
ensure_secret POSTGRES_PASSWORD "$APP_DIR/secrets/postgres_password" 32
ensure_secret OMNIKESTRAPIM_API_KEY "" 32
ensure_secret OMNIKESTRAPIM_SESSION_SECRET "" 32
ensure_secret OMNIKESTRAPIM_ADMIN_PASSWORD "" 24

if [ "${ENABLE_MARKETPLACE:-false}" = "true" ]; then
  if [ ! -s "$APP_DIR/secrets/marketplace_token_key" ]; then
    openssl rand -base64 32 | tr '+/' '-_' > "$APP_DIR/secrets/marketplace_token_key"
  fi
  chmod 0440 "$APP_DIR/secrets/marketplace_token_key"
  chown 568:568 "$APP_DIR/secrets/marketplace_token_key"
  ensure_secret marketplace_action_token "$APP_DIR/secrets/marketplace_action_token" 32
  if [ ! -s "$APP_DIR/secrets/mercadolivre_client_id" ]; then
    read -r -p "Mercado Livre client ID: " MELI_CLIENT_ID
    printf '%s' "$MELI_CLIENT_ID" > "$APP_DIR/secrets/mercadolivre_client_id"
  fi
  if [ ! -s "$APP_DIR/secrets/mercadolivre_client_secret" ]; then
    read -r -p "Mercado Livre client secret: " MELI_CLIENT_SECRET
    printf '%s' "$MELI_CLIENT_SECRET" > "$APP_DIR/secrets/mercadolivre_client_secret"
  fi
  chmod 0440 "$APP_DIR/secrets"/mercadolivre_* "$APP_DIR/secrets"/marketplace_*
  chown 568:568 "$APP_DIR/secrets"/mercadolivre_* "$APP_DIR/secrets"/marketplace_* 2>/dev/null || true
fi

if [ "$ACTION" = "upgrade" ]; then
  if [ "$SKIP_SNAPSHOT" = false ] && [ -z "$ROOT_DS" ]; then
    echo "ZFS indisponível; use --skip-snapshot ou prepare datasets ZFS." >&2
    exit 1
  fi
  bash "$SCRIPT_DIR/backup-truenas.sh" || exit 1
  if [ "$SKIP_SNAPSHOT" = false ]; then
    stamp="$(date +%Y%m%d-%H%M%S)"
    for dataset_path in "$APP_DIR/postgres" "$PRODUCT_DIR"; do
      dataset="$(zfs list -H -o name "$dataset_path" 2>/dev/null)" || exit 1
      zfs snapshot "${dataset}@pre-upgrade-$stamp" || exit 1
    done
  fi
fi

compose config --quiet
compose pull
compose up -d

HEALTH_HOST="$([ "$BIND_IP" = "0.0.0.0" ] && echo "127.0.0.1" || echo "$BIND_IP")"
for attempt in $(seq 1 60); do
  health="$(curl -fsS "http://$HEALTH_HOST:$PORT/api/health" 2>/dev/null)" || true
  if [ -n "$health" ]; then
    READY="$(curl -s -o /dev/null -w '%{http_code}' "http://$HEALTH_HOST:$PORT/api/ready" || true)"
    [ "$READY" = "200" ] || {
      echo "AVISO: pasta de produtos não pronta (/api/ready=$READY). Verifique UID 568 em $PRODUCT_DIR." >&2
      exit 3
    }
    echo "OmnIkestrAPIM iniciado em http://$HEALTH_HOST:$PORT"
    if [[ " $GENERATED_KEYS " == *" OMNIKESTRAPIM_ADMIN_PASSWORD "* ]]; then
      echo "Senha inicial do administrador: $(get_env OMNIKESTRAPIM_ADMIN_PASSWORD)"
    else
      echo "Senha mantida; consulte docker/.env."
    fi
    echo "$health"
    exit 0
  fi
  sleep 2
done

docker logs --tail 100 "$(docker ps --format '{{.Names}}' | grep backend | head -n1)" 2>/dev/null || true
echo "O backend não respondeu em 120 segundos." >&2
exit 1
