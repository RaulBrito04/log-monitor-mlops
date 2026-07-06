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


def build_baseline_report(runtime_metrics: dict[str, Any], generated_at: str | None = None) -> str:
    generated_ts = generated_at or datetime.now(timezone.utc).isoformat()
    throughput_components = runtime_metrics.get("throughput_components", {})

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
            ("fetch_duration_seconds", "Fetch duration"),
            ("feature_duration_seconds", "Feature engineering duration"),
            ("evaluation_duration_seconds", "Evaluation duration"),
            ("rules_duration_seconds", "Rules duration"),
            ("commit_duration_seconds", "Commit duration"),
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
