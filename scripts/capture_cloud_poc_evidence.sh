#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT_DIR/docker/docker-compose.cloud-poc.yml}"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env.cloud}"
TIMESTAMP="${TIMESTAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT_DIR/docs/cloud_poc_evidence/$TIMESTAMP}"
REPORT_FILE="$OUTPUT_DIR/CLOUD_POC_LIVE_EVIDENCE.md"
SCREENSHOT_DIR="$OUTPUT_DIR/screenshots"
FENCE='```'

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR" "$SCREENSHOT_DIR"

set -a
source "$ENV_FILE"
set +a

: "${API_DOMAIN:?API_DOMAIN is required}"
: "${DASHBOARD_DOMAIN:?DASHBOARD_DOMAIN is required}"
: "${GRAFANA_DOMAIN:?GRAFANA_DOMAIN is required}"

capture_command() {
  local name="$1"
  shift
  local file="$OUTPUT_DIR/${name}.txt"
  set +e
  {
    printf '$ '
    printf '%q ' "$@"
    printf '\n\n'
    "$@"
  } >"$file" 2>&1
  local status=$?
  set -e
  printf '%s\n' "$status" > "$OUTPUT_DIR/${name}.exitcode"
}

read_status() {
  local name="$1"
  cat "$OUTPUT_DIR/${name}.exitcode"
}

status_label() {
  local code="$1"
  if [[ "$code" == "0" ]]; then
    printf 'PASS'
  else
    printf 'FAIL'
  fi
}

append_block() {
  local title="$1"
  local file="$2"
  {
    printf '\n## %s\n' "$title"
    printf '%stext\n' "$FENCE"
    cat "$file"
    printf '\n%s\n' "$FENCE"
  } >> "$REPORT_FILE"
}

capture_command compose_ps docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps
capture_command api_health curl -fsS --connect-timeout 5 --max-time 15 "https://${API_DOMAIN}/health"
capture_command dashboard_health curl -fsS --connect-timeout 5 --max-time 15 "https://${DASHBOARD_DOMAIN}/_stcore/health"
capture_command grafana_login_headers curl -fsSI --connect-timeout 5 --max-time 15 "https://${GRAFANA_DOMAIN}/login"
capture_command host_listeners ss -ltn

if command -v systemctl >/dev/null 2>&1; then
  set +e
  systemctl list-unit-files --type=service 2>/dev/null | grep -q '^logmonitor-cloud\.service'
  has_unit=$?
  set -e
  if [[ "$has_unit" == "0" ]]; then
    capture_command systemd_status systemctl status logmonitor-cloud.service --no-pager
  fi
fi

capture_command smoke_test bash "$ROOT_DIR/scripts/cloud_poc_smoke_test.sh"

private_port_check_file="$OUTPUT_DIR/private_port_check.txt"
private_port_status=0
{
  printf 'Host private-port exposure check\n\n'
  for port in 5432 5000 5001 8501 9090 9093; do
    if ss -ltn "( sport = :$port )" | grep -q LISTEN; then
      printf 'FAIL: port %s is listening on the host\n' "$port"
      private_port_status=1
    else
      printf 'OK: port %s is not listening on the host\n' "$port"
    fi
  done
} > "$private_port_check_file"
printf '%s\n' "$private_port_status" > "$OUTPUT_DIR/private_port_check.exitcode"

compose_status="$(status_label "$(read_status compose_ps)")"
api_status="$(status_label "$(read_status api_health)")"
dashboard_status="$(status_label "$(read_status dashboard_health)")"
grafana_status="$(status_label "$(read_status grafana_login_headers)")"
smoke_status="$(status_label "$(read_status smoke_test)")"
private_port_label="$(status_label "$(read_status private_port_check)")"

captured_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
host_name="$(hostname)"

cat > "$REPORT_FILE" <<EOF
# Cloud POC Live Evidence

## Summary
- Captured at (UTC): $captured_at
- Host: $host_name
- Compose file: $COMPOSE_FILE
- Env file: $ENV_FILE
- API domain: https://$API_DOMAIN
- Dashboard domain: https://$DASHBOARD_DOMAIN
- Grafana domain: https://$GRAFANA_DOMAIN
- Compose status capture: $compose_status
- API health check: $api_status
- Dashboard health check: $dashboard_status
- Grafana login headers: $grafana_status
- Smoke test: $smoke_status
- Private host-port check: $private_port_label
EOF

{
  printf '\n## Manual screenshots to add\n'
  printf 'Place screenshots in `%s`\n' "$SCREENSHOT_DIR"
  printf '%s\n' '- `01_api_health.png`'
  printf '%s\n' '- `02_dashboard.png`'
  printf '%s\n' '- `03_grafana_login.png`'
} >> "$REPORT_FILE"

append_block "Compose status" "$OUTPUT_DIR/compose_ps.txt"
append_block "API health" "$OUTPUT_DIR/api_health.txt"
append_block "Dashboard health" "$OUTPUT_DIR/dashboard_health.txt"
append_block "Grafana login headers" "$OUTPUT_DIR/grafana_login_headers.txt"
append_block "Smoke test" "$OUTPUT_DIR/smoke_test.txt"
append_block "Host listeners" "$OUTPUT_DIR/host_listeners.txt"
append_block "Private port exposure check" "$OUTPUT_DIR/private_port_check.txt"

if [[ -f "$OUTPUT_DIR/systemd_status.txt" ]]; then
  append_block "systemd status" "$OUTPUT_DIR/systemd_status.txt"
fi

echo "Evidence package written to: $OUTPUT_DIR"
echo "Main report: $REPORT_FILE"