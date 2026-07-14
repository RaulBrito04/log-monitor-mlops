#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-./venv/bin/python}"
PYTEST_BIN="${PYTEST_BIN:-$PYTHON_BIN -m pytest}"
ALEMBIC_BIN="${ALEMBIC_BIN:-./venv/bin/alembic}"
COMPOSE_FILE="docker/docker-compose.yml"

SKIP_INTEGRATION="${SKIP_INTEGRATION:-0}"
SKIP_DOCKER="${SKIP_DOCKER:-0}"
SKIP_DATASET_QUALITY="${SKIP_DATASET_QUALITY:-0}"

printf '
[1/6] Syntax validation (py_compile)
'
"$PYTHON_BIN" -m py_compile $(find src tests scripts -name '*.py' -not -path '*/__pycache__/*')

if [[ "$SKIP_DATASET_QUALITY" != "1" ]]; then
  printf '
[2/6] Dataset quality checks
'
  "$PYTHON_BIN" scripts/quality_checks.py
else
  printf '
[2/6] Dataset quality checks skipped
'
fi

printf '
[3/6] Unit, app and dashboard suites
'
$PYTEST_BIN tests/unit tests/test_flask_app.py tests/test_dashboard.py tests/test_mlflow.py -q

if [[ "$SKIP_INTEGRATION" != "1" ]]; then
  printf '
[4/6] Integration suite
'
  $PYTEST_BIN tests/integration -q -m integration --no-cov
else
  printf '
[4/6] Integration suite skipped
'
fi

printf '
[5/6] Alembic migration check
'
"$ALEMBIC_BIN" upgrade head >/dev/null
"$ALEMBIC_BIN" current

if [[ "$SKIP_DOCKER" != "1" ]]; then
  printf '
[6/6] Docker Compose config validation
'
  docker compose -f "$COMPOSE_FILE" config >/dev/null
  printf 'docker compose config: OK
'
else
  printf '
[6/6] Docker Compose config validation skipped
'
fi

printf '
Final quality gate completed successfully.
'
