#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from scripts.generate_throughput_baseline_report import main as generate_baseline_report
from src.log_processor.ingester import ingest_from_file
from src.ml.realtime_hybrid import RealtimeHybridProcessor
from src.monitoring.metrics import persist_component_runtime_metrics, persist_runtime_metrics

DEFAULT_SOURCES = [
    PROJECT_ROOT / "logs" / "app.log.backup",
    PROJECT_ROOT / "logs" / "app.log",
]
DEFAULT_REPLAY_OUTPUT = PROJECT_ROOT / "tmp" / "week19_baseline_replay.log"


def db_config() -> dict[str, object]:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", 5432)),
        "database": os.getenv("POSTGRES_DB", "logmonitor"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "changeme"),
    }


def query_scalar(sql: str, params: tuple[object, ...] = ()) -> int:
    conn = psycopg2.connect(**db_config())
    cur = conn.cursor()
    cur.execute(sql, params)
    value = cur.fetchone()[0]
    cur.close()
    conn.close()
    return int(value or 0)


def build_replay_log(sources: Sequence[Path], output_path: Path, target_lines: int) -> dict[str, object]:
    source_files: list[str] = []
    source_lines: list[str] = []

    for path in sources:
        if not path.exists():
            continue
        lines = [line for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
        if not lines:
            continue
        source_files.append(str(path.relative_to(PROJECT_ROOT)))
        source_lines.extend(lines)

    if not source_lines:
        raise FileNotFoundError("No usable source log lines were found for the Week 19 replay benchmark.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for idx in range(target_lines):
            handle.write(source_lines[idx % len(source_lines)] + "\n")

    return {
        "source_files": source_files,
        "source_line_pool": len(source_lines),
        "replay_file": str(output_path.relative_to(PROJECT_ROOT)),
        "replay_lines": target_lines,
    }


def collect_realtime_baseline(fetch_limit: int, poll_interval_seconds: float, start_from_log_id: int) -> dict[str, object]:
    processor = RealtimeHybridProcessor(poll_interval_sec=poll_interval_seconds, fetch_limit=fetch_limit)
    processor.last_processed_id = max(processor.last_processed_id, start_from_log_id)
    cycles = 0
    logs_processed = 0
    anomalies_found = 0
    total_fetch = 0.0
    total_feature = 0.0
    total_evaluation = 0.0
    started = time.perf_counter()

    try:
        while True:
            new_logs, fetch_duration = processor.fetch_new_logs()
            total_fetch += fetch_duration
            if new_logs.empty:
                break

            summary = processor.process_logs(new_logs)
            cycles += 1
            logs_processed += int(summary["logs_processed"])
            anomalies_found += int(summary["anomalies_found"])
            total_feature += float(summary["feature_duration_seconds"])
            total_evaluation += float(summary["evaluation_duration_seconds"])

        elapsed = time.perf_counter() - started
        throughput = logs_processed / elapsed if elapsed > 0 else 0.0
        remaining = query_scalar("select count(*) from raw_logs where id > %s", (processor.last_processed_id,))
        configured_ceiling = fetch_limit / poll_interval_seconds if poll_interval_seconds > 0 else None

        snapshot = {
            "logs_processed": logs_processed,
            "anomalies_found": anomalies_found,
            "cycles": cycles,
            "duration_seconds": round(elapsed, 6),
            "throughput_logs_per_second": round(throughput, 6),
            "fetch_duration_seconds": round(total_fetch, 6),
            "feature_duration_seconds": round(total_feature, 6),
            "evaluation_duration_seconds": round(total_evaluation, 6),
            "configured_fetch_limit": fetch_limit,
            "configured_poll_interval_seconds": round(poll_interval_seconds, 3),
            "configured_ceiling_logs_per_second": round(configured_ceiling, 6) if configured_ceiling is not None else None,
            "remaining_unprocessed_logs": remaining,
        }
        persist_component_runtime_metrics("realtime_hybrid", snapshot)
        return snapshot
    finally:
        processor.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect a Week 19 throughput baseline using replayed project logs.")
    parser.add_argument("--target-lines", type=int, default=5000, help="Target replay file size in lines (default: 5000)")
    parser.add_argument("--batch-size", type=int, default=500, help="Ingestion batch size (default: 500)")
    parser.add_argument("--realtime-fetch-limit", type=int, default=1000, help="Current realtime fetch limit in code (default: 1000)")
    parser.add_argument("--realtime-poll-interval", type=float, default=2.0, help="Configured realtime poll interval in seconds (default: 2)")
    parser.add_argument("--replay-output", default=str(DEFAULT_REPLAY_OUTPUT), help="Replay log output path")
    parser.add_argument("--source-log", action="append", default=[], help="Additional source log path(s) to seed the replay file")
    args = parser.parse_args()

    source_paths = [Path(path) if Path(path).is_absolute() else PROJECT_ROOT / path for path in args.source_log]
    if not source_paths:
        source_paths = DEFAULT_SOURCES

    replay_output = Path(args.replay_output)
    if not replay_output.is_absolute():
        replay_output = PROJECT_ROOT / replay_output

    started_at = datetime.now(timezone.utc).isoformat()
    before_raw_logs = query_scalar("select count(*) from raw_logs")
    before_raw_max = query_scalar("select coalesce(max(id), 0) from raw_logs")
    before_hybrid_max = query_scalar("select coalesce(max(log_id), 0) from hybrid_scores")

    replay_meta = build_replay_log(source_paths, replay_output, args.target_lines)
    ingest_from_file(str(replay_output), batch_size=args.batch_size, log_format="json")

    after_raw_logs = query_scalar("select count(*) from raw_logs")
    after_raw_max = query_scalar("select coalesce(max(id), 0) from raw_logs")

    realtime_snapshot = collect_realtime_baseline(
        fetch_limit=args.realtime_fetch_limit,
        poll_interval_seconds=args.realtime_poll_interval,
        start_from_log_id=before_raw_max,
    )
    after_hybrid_max = query_scalar("select coalesce(max(log_id), 0) from hybrid_scores")

    benchmark_meta = {
        "started_at": started_at,
        **replay_meta,
        "ingester_batch_size": args.batch_size,
        "realtime_fetch_limit": args.realtime_fetch_limit,
        "realtime_poll_interval_seconds": args.realtime_poll_interval,
        "configured_ceiling_logs_per_second": round(args.realtime_fetch_limit / args.realtime_poll_interval, 6) if args.realtime_poll_interval > 0 else None,
        "throughput_measurement_mode": "active_processing_no_sleep",
        "throughput_measurement_note": "Week 19 collector measures active processing throughput without inter-cycle sleep; the steady-state daemon loop remains bounded by fetch_limit / poll_interval.",
        "raw_logs_before": before_raw_logs,
        "raw_logs_after": after_raw_logs,
        "hybrid_scores_before": before_hybrid_max,
        "hybrid_scores_after": after_hybrid_max,
        "raw_log_max_id_before": before_raw_max,
        "raw_log_max_id_after": after_raw_max,
        "benchmark_start_log_id": before_raw_max,
        "rule_engine_status": "skipped",
        "rule_engine_reason": "Manual rule_engine invocation was skipped to avoid duplicating alerts on the shared development dataset; a background realtime rule-engine service may still create alerts while the stack is running.",
        "realtime_cycles": realtime_snapshot["cycles"],
        "realtime_logs_processed": realtime_snapshot["logs_processed"],
        "realtime_anomalies_found": realtime_snapshot["anomalies_found"],
    }
    persist_runtime_metrics({"throughput_benchmark": benchmark_meta})
    generate_baseline_report()

    print("Week 19 baseline collection complete")
    print(f"Replay file: {benchmark_meta['replay_file']} ({benchmark_meta['replay_lines']} lines)")
    print(f"Raw logs before/after: {before_raw_logs} -> {after_raw_logs}")
    print(f"Hybrid max log id before/after: {before_hybrid_max} -> {after_hybrid_max}")
    print(f"Realtime throughput: {realtime_snapshot['throughput_logs_per_second']} logs/s across {realtime_snapshot['cycles']} cycles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
