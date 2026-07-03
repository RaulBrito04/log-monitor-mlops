#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-./venv/bin/python}"
CICIDS_INPUT="${CICIDS_INPUT:-${1:-data/cicids}}"
REPORT_JSON="${CICIDS_REPORT_JSON:-experiments/cicids_benchmark_report.json}"
REPORT_MD="${CICIDS_REPORT_MD:-experiments/cicids_benchmark_report.md}"
DOC_RESULTS="${CICIDS_DOC_RESULTS:-docs/CICIDS_BENCHMARK_RESULTS.md}"
RANDOM_STATE="${CICIDS_RANDOM_STATE:-42}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python runner not found or not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -e "$CICIDS_INPUT" ]]; then
  cat >&2 <<EOF
CICIDS input path not found: $CICIDS_INPUT

Expected either:
- a directory containing CICIDS CSV files
- or a single CICIDS CSV file

Examples:
  bash scripts/run_cicids_benchmark.sh data/cicids
  CICIDS_INPUT=data/cicids/Friday-WorkingHours-Afternoon-DDos.pcap_ISCX.csv bash scripts/run_cicids_benchmark.sh
EOF
  exit 1
fi

mkdir -p "$(dirname "$REPORT_JSON")" "$(dirname "$REPORT_MD")" "$(dirname "$DOC_RESULTS")"

echo "[1/3] Running CICIDS benchmark"
"$PYTHON_BIN" -m src.ml.cicids_benchmark \
  --input "$CICIDS_INPUT" \
  --report-path "$REPORT_JSON" \
  --markdown-path "$REPORT_MD" \
  --random-state "$RANDOM_STATE"

echo "[2/3] Copying markdown summary to docs"
cp "$REPORT_MD" "$DOC_RESULTS"

echo "[3/3] Benchmark complete"
echo "- JSON report: $REPORT_JSON"
echo "- Markdown report: $REPORT_MD"
echo "- Docs copy: $DOC_RESULTS"
