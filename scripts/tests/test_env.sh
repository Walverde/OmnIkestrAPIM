#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
source "$PROJECT_DIR/scripts/lib/env.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
ENV_FILE="$TMP_DIR/test.env"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

ensure_secret TEST_API_KEY "" 32
FIRST="$(get_env TEST_API_KEY)"
ensure_secret TEST_API_KEY "" 32
test "$FIRST" = "$(get_env TEST_API_KEY)"

set_env EXISTING "preserved"
set_env SPECIAL "a|b&c"
test "$(get_env EXISTING)" = "preserved"
test "$(get_env SPECIAL)" = "a|b&c"

printf '%s' "file-wins" > "$TMP_DIR/secret"
set_env TEST_SECRET "env-wins"
ensure_secret TEST_SECRET "$TMP_DIR/secret" 32
test "$(get_env TEST_SECRET)" = "file-wins"

echo "env.sh tests passed"
