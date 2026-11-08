#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "Configurando o ambiente do OmnIkestrAPIM..."

if ! command -v docker >/dev/null 2>&1; then
  echo "Erro: Docker não está instalado ou não está no PATH." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Erro: o plugin Docker Compose v2 não está disponível." >&2
  exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "Erro: OpenSSL é necessário para gerar a senha do banco." >&2
  exit 1
fi

if [ -n "${SUDO_UID:-}" ]; then
  default_puid="$SUDO_UID"
  default_pgid="${SUDO_GID:-$(id -g "$SUDO_UID")}"
else
  default_puid="$(id -u)"
  default_pgid="$(id -g)"
fi

if [ "$default_puid" = "0" ]; then
  echo "Erro: execute como usuário comum com acesso ao Docker, não como root." >&2
  exit 1
fi

if [ -L .env ]; then
  echo "Erro: .env não pode ser um link simbólico." >&2
  exit 1
fi

mkdir -p ./data/products

if [ ! -e .env ]; then
  (umask 077; set -o noclobber; : > .env) 2>/dev/null || {
    echo "Erro: não foi possível criar o arquivo .env." >&2
    exit 1
  }
fi
chmod 600 .env

ensure_default() {
  local key="$1"
  local value="$2"
  if ! grep -Eq "^${key}=.+$" .env; then
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

ensure_default POSTGRES_DB omnikestr
ensure_default POSTGRES_USER omnikestr
if ! grep -Eq '^POSTGRES_PASSWORD=.+$' .env; then
  ensure_default POSTGRES_PASSWORD "$(openssl rand -hex 32)"
fi
ensure_default PRODUCTS_DIR ../data/products
ensure_default PRODUCTS_ROOT /data/products
ensure_default PRODUCT_ROOTS /data/products
ensure_default WATCH_DIRS /data/products
ensure_default PUID "$default_puid"
ensure_default PGID "$default_pgid"

if [ -s .env ]; then
  if [ -f .env ]; then
    echo "Configuração .env verificada; valores existentes foram mantidos e padrões ausentes adicionados."
  else
    echo "Erro: .env precisa ser um arquivo regular." >&2
    exit 1
  fi
fi

docker compose -f docker/docker-compose.yml up -d --build

echo "OmnIkestrAPIM iniciado em segundo plano."
echo "Acesse http://localhost:8000 para concluir a configuração inicial."
