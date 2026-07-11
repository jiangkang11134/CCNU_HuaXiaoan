#!/usr/bin/env bash

set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.prod"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "Please run this script from the Yuxi project root." >&2
  exit 1
fi

echo "========================================"
echo "1. Prepare production environment"
echo "========================================"
bash scripts/init_prod_env.sh "$ENV_FILE"

echo "========================================"
echo "2. Stop old Yuxi containers"
echo "========================================"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down --remove-orphans

echo "========================================"
echo "3. Update code"
echo "========================================"
git pull

echo "========================================"
echo "4. Rebuild and start containers"
echo "========================================"
bash scripts/init_prod_env.sh "$ENV_FILE"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build

echo "========================================"
echo "5. Health check"
echo "========================================"
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" ps
for attempt in $(seq 1 60); do
  if curl --fail --silent --show-error http://127.0.0.1/api/system/health; then
    printf '\n'
    break
  fi
  if [ "$attempt" -eq 60 ]; then
    echo "API health check failed after 120 seconds." >&2
    docker logs --tail=120 api-prod >&2 || true
    exit 1
  fi
  sleep 2
done

echo "========================================"
echo "Deployment completed"
echo "========================================"
