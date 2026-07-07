from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _format_float(value: Any, digits: int = 3) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _format_int(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value)}"
    except (TypeError, ValueError):
        return "-"


def _format_list(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "-"
    return ", ".join(str(item) for item in value)


def build_baseline_report(runtime_metrics: dict[str, Any], generated_at: str | None = None) -> str:
    generated_ts = generated_at or datetime.now(timezone.utc).isoformat()
    throughput_components = runtime_metrics.get("throughput_components", {})
    benchmark = runtime_metrics.get("throughput_benchmark", {})

    lines = [
        "# Week 19 Throughput Baseline Results",
        "",
        f"Generated at: `{generated_ts}`",
        "",
        "## Scope",
        "",
        "This report summarizes the latest persisted throughput snapshots captured by the Week 19 instrumentation layer.",
        "",
    ]

    if isinstance(benchmark, dict) and benchmark:
        lines.extend(
            [
                "## Benchmark Config",
                "",
                f"- Started at: `{benchmark.get('started_at', '-')}`",
                f"- Source files: `{_format_list(benchmark.get('source_files'))}`",
                f"- Replay file: `{benchmark.get('replay_file', '-')}`",
                f"- Replay lines: `{_format_int(benchmark.get('replay_lines'))}`",
                f"- Source line pool: `{_format_int(benchmark.get('source_line_pool'))}`",
                f"- Ingestion batch size: `{_format_int(benchmark.get('ingester_batch_size'))}`",
                f"- Realtime fetch limit: `{_format_int(benchmark.get('realtime_fetch_limit'))}`",
                f"- Realtime poll interval: `{_format_float(benchmark.get('realtime_poll_interval_seconds'))}` s",
                f"- Configured ceiling: `{_format_float(benchmark.get('configured_ceiling_logs_per_second'))}` logs/s",
                f"- Measurement mode: `{benchmark.get('throughput_measurement_mode', '-')}`",
                f"- Measurement note: `{benchmark.get('throughput_measurement_note', '-')}`",
                f"- Raw logs before run: `{_format_int(benchmark.get('raw_logs_before'))}`",
                f"- Benchmark start log id: `{_format_int(benchmark.get('benchmark_start_log_id'))}`",
                f"- Raw logs after run: `{_format_int(benchmark.get('raw_logs_after'))}`",
                f"- Hybrid max log id before run: `{_format_int(benchmark.get('hybrid_scores_before'))}`",
                f"- Hybrid max log id after run: `{_format_int(benchmark.get('hybrid_scores_after'))}`",
                f"- Rule engine status: `{benchmark.get('rule_engine_status', '-')}`",
                f"- Rule engine note: `{benchmark.get('rule_engine_reason', '-')}`",
                "",
            ]
        )

    if not isinstance(throughput_components, dict) or not throughput_components:
        lines.extend(
            [
                "## Status",
                "",
                "No throughput component snapshots were found in `data/runtime_metrics.json`.",
                "",
                "## Next Step",
                "",
                "1. Run the ingester, realtime hybrid processor, or hybrid pipeline with the new instrumentation enabled.",
                "2. Re-run `python scripts/generate_throughput_baseline_report.py` to capture measured values.",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "## Component Summary",
            "",
            "| Component | Recorded At | Duration (s) | Throughput (logs/s) | Batch/Logs | Errors |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )

    throughput_pairs: list[tuple[str, float]] = []
    for component in sorted(throughput_components):
        snapshot = throughput_components[component]
        if not isinstance(snapshot, dict):
            continue

        batch_or_logs = snapshot.get("logs_processed", snapshot.get("batch_size", snapshot.get("logs_ingested")))
        errors = snapshot.get("failed_records", snapshot.get("error_count", 0))
        throughput_value = snapshot.get("throughput_logs_per_second")
        if throughput_value not in (None, ""):
            try:
                throughput_pairs.append((component, float(throughput_value)))
            except (TypeError, ValueError):
                pass

        lines.append(
            "| {component} | {recorded_at} | {duration} | {throughput} | {batch_size} | {errors} |".format(
                component=component,
                recorded_at=snapshot.get("recorded_at", "-"),
                duration=_format_float(snapshot.get("duration_seconds")),
                throughput=_format_float(throughput_value),
                batch_size=_format_int(batch_or_logs),
                errors=_format_int(errors),
            )
        )

    if throughput_pairs:
        slowest_component, slowest_value = min(throughput_pairs, key=lambda item: item[1])
        fastest_component, fastest_value = max(throughput_pairs, key=lambda item: item[1])
        lines.extend(
            [
                "",
                "## Highlights",
                "",
                f"- Highest observed throughput: `{fastest_component}` at `{fastest_value:.3f}` logs/s.",
                f"- Lowest observed throughput: `{slowest_component}` at `{slowest_value:.3f}` logs/s.",
            ]
        )

        realtime_snapshot = throughput_components.get("realtime_hybrid")
        configured_ceiling = benchmark.get("configured_ceiling_logs_per_second") if isinstance(benchmark, dict) else None
        if isinstance(realtime_snapshot, dict) and configured_ceiling not in (None, ""):
            try:
                active_throughput = float(realtime_snapshot.get("throughput_logs_per_second"))
                ceiling_value = float(configured_ceiling)
                if ceiling_value > 0:
                    headroom_ratio = active_throughput / ceiling_value
                    if headroom_ratio >= 1.0:
                        lines.append(
                            f"- Active processing throughput exceeds the configured steady-state ceiling by `{((headroom_ratio - 1.0) * 100):.1f}%`."
                        )
                    else:
                        lines.append(
                            f"- Active processing throughput is `{((1.0 - headroom_ratio) * 100):.1f}%` below the configured steady-state ceiling."
                        )
            except (TypeError, ValueError):
                pass

    for component in sorted(throughput_components):
        snapshot = throughput_components[component]
        if not isinstance(snapshot, dict):
            continue

        lines.extend(
            [
                "",
                f"## {component}",
                "",
                f"- Recorded at: `{snapshot.get('recorded_at', '-')}`",
                f"- Duration: `{_format_float(snapshot.get('duration_seconds'))}` s",
                f"- Throughput: `{_format_float(snapshot.get('throughput_logs_per_second'))}` logs/s",
            ]
        )

        optional_fields = [
            ("logs_ingested", "Logs ingested"),
            ("logs_processed", "Logs processed"),
            ("anomalies_found", "Anomalies found"),
            ("failed_records", "Failed records"),
            ("configured_batch_size", "Configured batch size"),
            ("configured_fetch_limit", "Configured fetch limit"),
            ("configured_poll_interval_seconds", "Configured poll interval"),
            ("configured_ceiling_logs_per_second", "Configured ceiling"),
            ("cycles", "Cycles executed"),
            ("remaining_unprocessed_logs", "Remaining unprocessed logs"),
            ("fetch_duration_seconds", "Fetch duration"),
            ("feature_duration_seconds", "Feature engineering duration"),
            ("evaluation_duration_seconds", "Evaluation duration"),
            ("rules_duration_seconds", "Rules duration"),
            ("commit_duration_seconds", "Commit duration"),
            ("source_file", "Source file"),
        ]
        for field_name, label in optional_fields:
            if field_name in snapshot:
                value = snapshot[field_name]
                if isinstance(value, float):
                    rendered = _format_float(value)
                else:
                    rendered = str(value)
                lines.append(f"- {label}: `{rendered}`")

    return "\n".join(lines) + "\n"
