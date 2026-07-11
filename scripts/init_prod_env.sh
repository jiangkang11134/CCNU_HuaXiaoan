#!/usr/bin/env bash

set -euo pipefail

ENV_FILE="${1:-.env.prod}"
TEMPLATE_FILE=".env.template"
DEFAULT_MINERU_API_URI="${MINERU_API_URI:-https://mineru.net/api/v4}"

resolve_default_data_dir() {
  if [ -n "${YUXI_DATA_DIR:-}" ]; then
    printf '%s' "$YUXI_DATA_DIR"
    return
  fi
  printf '%s' "./docker/volumes"
}

generate_hex() {
  local length="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$length"
    return
  fi
  tr -dc 'a-f0-9' < /dev/urandom | head -c $((length * 2))
}

ensure_env_file() {
  if [ -f "$ENV_FILE" ]; then
    return
  fi
  if [ ! -f "$TEMPLATE_FILE" ]; then
    echo "Missing $TEMPLATE_FILE" >&2
    exit 1
  fi
  cp "$TEMPLATE_FILE" "$ENV_FILE"
}

get_env_value() {
  local key="$1"
  local line
  line=$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)
  printf '%s' "${line#*=}"
}

set_env_value() {
  local key="$1"
  local value="$2"
  local tmp_file
  tmp_file=$(mktemp)
  if grep -Eq "^${key}=" "$ENV_FILE"; then
    awk -v key="$key" -v value="$value" '
      $0 ~ "^" key "=" { print key "=" value; next }
      { print }
    ' "$ENV_FILE" > "$tmp_file"
    mv "$tmp_file" "$ENV_FILE"
  else
    rm -f "$tmp_file"
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

ensure_non_empty() {
  local key="$1"
  local value="$2"
  if [ -z "$(get_env_value "$key")" ]; then
    set_env_value "$key" "$value"
  fi
}

ensure_env_file

set_env_value "YUXI_ENV" "production"
ensure_non_empty "JWT_SECRET_KEY" "$(generate_hex 32)"
ensure_non_empty "YUXI_INSTANCE_ID" "instance-$(generate_hex 8)"
ensure_non_empty "YUXI_DATA_DIR" "$(resolve_default_data_dir)"
ensure_non_empty "MINERU_API_URI" "$DEFAULT_MINERU_API_URI"

mkdir -p "$(get_env_value YUXI_DATA_DIR)"

echo "Production env ready: $ENV_FILE"
echo "YUXI_DATA_DIR=$(get_env_value YUXI_DATA_DIR)"
