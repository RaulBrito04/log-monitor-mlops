#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-./venv/bin/python}"
CICIDS_INPUT="${CICIDS_INPUT:-${1:-data/cicids}}"
REPORT_JSON="${CICIDS_REPORT_JSON:-experiments/cicids_benchmark_report.json}"
REPORT_MD="${CICIDS_REPORT_MD:-experiments/cicids_benchmark_report.md}"
DOC_RESULTS="${CICIDS_DOC_RESULTS:-docs/CICIDS_BENCHMARK_RESULTS.md}"
OUTPUT_DIR="${CICIDS_OUTPUT_DIR:-experiments/cicids}"
LOG_DIR="${CICIDS_LOG_DIR:-$OUTPUT_DIR/logs}"
FAILURES_TSV="${CICIDS_FAILURES_TSV:-$OUTPUT_DIR/failures.tsv}"
RANDOM_STATE="${CICIDS_RANDOM_STATE:-42}"
MAX_TRAIN_ROWS="${CICIDS_MAX_TRAIN_ROWS:-60000}"
MAX_EVAL_ROWS="${CICIDS_MAX_EVAL_ROWS:-40000}"

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

mkdir -p "$(dirname "$REPORT_JSON")" "$(dirname "$REPORT_MD")" "$(dirname "$DOC_RESULTS")" "$OUTPUT_DIR" "$LOG_DIR"
: > "$FAILURES_TSV"

slugify() {
  local input="$1"
  input="${input%.csv}"
  input="${input// /_}"
  input="${input//[^A-Za-z0-9._-]/_}"
  printf '%s' "$input"
}

run_single() {
  local csv_path="$1"
  local json_path="$2"
  local md_path="$3"
  "$PYTHON_BIN" -m src.ml.cicids_benchmark \
    --input "$csv_path" \
    --report-path "$json_path" \
    --markdown-path "$md_path" \
    --random-state "$RANDOM_STATE" \
    --max-train-rows "$MAX_TRAIN_ROWS" \
    --max-eval-rows "$MAX_EVAL_ROWS"
}

if [[ -f "$CICIDS_INPUT" ]]; then
  echo "[1/3] Running CICIDS benchmark for single CSV"
  run_single "$CICIDS_INPUT" "$REPORT_JSON" "$REPORT_MD"

  echo "[2/3] Copying markdown summary to docs"
  cp "$REPORT_MD" "$DOC_RESULTS"

  echo "[3/3] Benchmark complete"
  echo "- JSON report: $REPORT_JSON"
  echo "- Markdown report: $REPORT_MD"
  echo "- Docs copy: $DOC_RESULTS"
  exit 0
fi

mapfile -t CSV_FILES < <(find "$CICIDS_INPUT" -type f -iname '*.csv' | sort)
if [[ ${#CSV_FILES[@]} -eq 0 ]]; then
  echo "No CSV files found under: $CICIDS_INPUT" >&2
  exit 1
fi

rm -f "$OUTPUT_DIR"/*.json "$OUTPUT_DIR"/*.md "$LOG_DIR"/*.log 2>/dev/null || true

TOTAL=${#CSV_FILES[@]}
INDEX=0
SUCCESS_COUNT=0
for csv_path in "${CSV_FILES[@]}"; do
  INDEX=$((INDEX + 1))
  base_name="$(basename "$csv_path")"
  slug="$(slugify "$base_name")"
  json_path="$OUTPUT_DIR/${slug}.json"
  md_path="$OUTPUT_DIR/${slug}.md"
  log_path="$LOG_DIR/${slug}.log"
  echo "[$INDEX/$TOTAL] Running CICIDS benchmark for $base_name"
  if run_single "$csv_path" "$json_path" "$md_path" > "$log_path" 2>&1; then
    SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
  else
    status=$?
    reason="$(grep -E "^[A-Za-z]+Error:" "$log_path" | tail -n 1 || true)"
    if [[ -z "$reason" ]]; then
      reason="$(tail -n 20 "$log_path" | tr '\n' ' ' | tr '\t' ' ' | sed 's/[[:space:]]\+/ /g' | cut -c1-400)"
    fi
    printf '%s\t%s\t%s\n' "$base_name" "$status" "$reason" >> "$FAILURES_TSV"
    echo "  skipped: exit_code=$status"
  fi
  sync
  sleep 1
done

export CICIDS_OUTPUT_DIR="$OUTPUT_DIR"
export CICIDS_DOC_RESULTS="$DOC_RESULTS"
export CICIDS_FAILURES_TSV="$FAILURES_TSV"
export CICIDS_MAX_TRAIN_ROWS="$MAX_TRAIN_ROWS"
export CICIDS_MAX_EVAL_ROWS="$MAX_EVAL_ROWS"
python3 - <<'PY'
import csv
import json
import os
from pathlib import Path

output_dir = Path(os.environ["CICIDS_OUTPUT_DIR"])
doc_path = Path(os.environ["CICIDS_DOC_RESULTS"])
failures_path = Path(os.environ["CICIDS_FAILURES_TSV"])
max_train_rows = os.environ["CICIDS_MAX_TRAIN_ROWS"]
max_eval_rows = os.environ["CICIDS_MAX_EVAL_ROWS"]
json_files = sorted(output_dir.glob("*.json"))
if not json_files:
    raise SystemExit("No successful CICIDS benchmark reports were generated")

rows = []
for path in json_files:
    report = json.loads(path.read_text(encoding="utf-8"))
    rows.append(
        {
            "file": path.stem,
            "rows": report["dataset"]["total_rows"],
            "attack_rows": report["dataset"]["attack_rows"],
            "iforest_f1": report["models"]["iforest"]["test"]["f1_score"],
            "iforest_roc_auc": report["models"]["iforest"]["test"]["roc_auc"],
            "rf_f1": report["models"]["random_forest"]["test"]["f1_score"],
            "rf_roc_auc": report["models"]["random_forest"]["test"]["roc_auc"],
            "ensemble_f1": report["models"]["ensemble"]["test"]["f1_score"],
            "ensemble_roc_auc": report["models"]["ensemble"]["test"]["roc_auc"],
            "train_rows": report["dataset"]["splits"]["train_rows"],
            "validation_rows": report["dataset"]["splits"]["validation_rows"],
            "test_rows": report["dataset"]["splits"]["test_rows"],
        }
    )

failures = []
if failures_path.exists():
    with failures_path.open(encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row in reader:
            if len(row) >= 3:
                failures.append({"file": row[0], "exit_code": row[1], "reason": row[2]})

best_ensemble = max(rows, key=lambda row: row["ensemble_f1"])
best_iforest = max(rows, key=lambda row: row["iforest_f1"])
mean_iforest = sum(row["iforest_f1"] for row in rows) / len(rows)
mean_ensemble = sum(row["ensemble_f1"] for row in rows) / len(rows)

lines = [
    "# CICIDS Benchmark Results",
    "",
    "## Estado",
    "",
    f"Benchmark executado com sucesso em {len(rows)} ficheiro(s) CICIDS-2017 e excluiu {len(failures)} ficheiro(s) que nao cumpriram o protocolo ou falharam no run.",
    "",
    "## Metodo usado",
    "",
    "- benchmark por ficheiro CSV, nunca concatenando o dataset inteiro num unico run",
    f"- split temporal train/validation/test com cap de {max_train_rows} rows no treino e {max_eval_rows} rows em validation/test para manter reproducibilidade em laptop/WSL",
    "- isolamento do benchmark externo face a pipeline HTTP operacional do projeto",
    "- artefactos detalhados por ficheiro guardados em `experiments/cicids/` e logs em `experiments/cicids/logs/`",
    "",
    "## Resultados por ficheiro",
    "",
    "| File | Rows | Attack Rows | Train | Val | Test | IF F1 | IF ROC-AUC | RF F1 | RF ROC-AUC | Ensemble F1 | Ensemble ROC-AUC |",
    "|------|------|-------------|-------|-----|------|-------|------------|-------|------------|-------------|------------------|",
]
for row in rows:
    lines.append(
        f"| {row['file']} | {row['rows']} | {row['attack_rows']} | {row['train_rows']} | {row['validation_rows']} | {row['test_rows']} | {row['iforest_f1']:.4f} | {row['iforest_roc_auc']:.4f} | {row['rf_f1']:.4f} | {row['rf_roc_auc']:.4f} | {row['ensemble_f1']:.4f} | {row['ensemble_roc_auc']:.4f} |"
    )

lines.extend([
    "",
    "## Destaques",
    "",
    f"- melhor F1 do Ensemble: `{best_ensemble['ensemble_f1']:.4f}` em `{best_ensemble['file']}`",
    f"- melhor F1 do Isolation Forest: `{best_iforest['iforest_f1']:.4f}` em `{best_iforest['file']}`",
    f"- media F1 do Ensemble nos runs bem-sucedidos: `{mean_ensemble:.4f}`",
    f"- media F1 do Isolation Forest nos runs bem-sucedidos: `{mean_iforest:.4f}`",
])

if failures:
    lines.extend([
        "",
        "## Ficheiros excluidos ou falhados",
        "",
        "| File | Exit Code | Reason |",
        "|------|-----------|--------|",
    ])
    for row in failures:
        reason = row['reason'].replace('|', '/').strip() or 'no error message captured'
        lines.append(f"| {row['file']} | {row['exit_code']} | {reason} |")

lines.extend([
    "",
    "## Notas",
    "",
    "- estes resultados sao de benchmark externo flow-based (CICIDS-2017)",
    "- nao substituem a validacao operacional com logs reais Nginx/Apache ja realizada no projeto",
    "- ficheiros benign-only ou que nao sustentem o protocolo temporal podem ser excluidos e isso deve ser explicado no relatorio",
])

doc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(doc_path)
PY

echo "[done] Per-file CICIDS benchmark complete"
echo "- Successful artifacts: $SUCCESS_COUNT"
echo "- Per-file artifacts: $OUTPUT_DIR"
echo "- Aggregate results doc: $DOC_RESULTS"
