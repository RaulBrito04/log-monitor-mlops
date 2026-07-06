#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.monitoring.throughput_report import build_baseline_report

RUNTIME_METRICS_FILE = PROJECT_ROOT / "data" / "runtime_metrics.json"
OUTPUT_PATH = PROJECT_ROOT / "docs" / "WEEK19_BASELINE_RESULTS.md"


def main() -> int:
    runtime_metrics = {}
    if RUNTIME_METRICS_FILE.exists():
        runtime_metrics = json.loads(RUNTIME_METRICS_FILE.read_text(encoding="utf-8-sig"))

    report = build_baseline_report(runtime_metrics)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(report, encoding="utf-8")
    print(f"Baseline report written to: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
