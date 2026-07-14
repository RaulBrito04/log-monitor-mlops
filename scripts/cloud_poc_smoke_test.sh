#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker/docker-compose.cloud-poc.yml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.cloud}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

: "${API_DOMAIN:?API_DOMAIN is required}"
: "${DASHBOARD_DOMAIN:?DASHBOARD_DOMAIN is required}"
: "${GRAFANA_DOMAIN:?GRAFANA_DOMAIN is required}"

echo "[1/4] Compose status"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

echo "[2/4] Public HTTPS checks"
curl -fsS --connect-timeout 5 --max-time 15 "https://${API_DOMAIN}/health"
curl -fsS --connect-timeout 5 --max-time 15 "https://${DASHBOARD_DOMAIN}/_stcore/health" >/dev/null
curl -fsSI --connect-timeout 5 --max-time 15 "https://${GRAFANA_DOMAIN}/login" >/dev/null

echo "[3/4] Host port exposure checks"
for port in 5432 5000 5001 8501 9090 9093; do
  if ss -ltn "( sport = :$port )" | grep -q LISTEN; then
    echo "Unexpected public host listener on port $port" >&2
    exit 1
  fi
done

echo "[4/4] Success"
echo "Cloud POC smoke test passed."
