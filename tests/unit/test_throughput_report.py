from __future__ import annotations

from src.monitoring.throughput_report import build_baseline_report


class TestThroughputReport:
    def test_build_baseline_report_handles_missing_snapshots(self):
        report = build_baseline_report({})

        assert "No throughput component snapshots were found" in report
        assert "generate_throughput_baseline_report.py" in report

    def test_build_baseline_report_renders_component_summary_and_benchmark_config(self):
        report = build_baseline_report(
            {
                "throughput_benchmark": {
                    "started_at": "2026-07-07T10:00:00+00:00",
                    "source_files": ["logs/app.log.backup", "logs/app.log"],
                    "replay_file": "tmp/week19_baseline_replay.log",
                    "replay_lines": 5000,
                    "source_line_pool": 3376,
                    "ingester_batch_size": 500,
                    "realtime_fetch_limit": 500,
                    "realtime_poll_interval_seconds": 30.0,
                    "configured_ceiling_logs_per_second": 16.667,
                    "throughput_measurement_mode": "active_processing_no_sleep",
                    "throughput_measurement_note": "Week 19 collector measures active processing throughput without inter-cycle sleep; the steady-state daemon loop remains bounded by fetch_limit / poll_interval.",
                    "raw_logs_before": 113514,
                    "raw_logs_after": 118514,
                    "hybrid_scores_before": 113597,
                    "hybrid_scores_after": 118597,
                    "rule_engine_status": "skipped",
                },
                "throughput_components": {
                    "ingester": {
                        "recorded_at": "2026-07-06T12:00:00+00:00",
                        "duration_seconds": 2.5,
                        "throughput_logs_per_second": 400.0,
                        "logs_ingested": 1000,
                        "failed_records": 4,
                    },
                    "realtime_hybrid": {
                        "recorded_at": "2026-07-06T12:01:00+00:00",
                        "duration_seconds": 4.0,
                        "throughput_logs_per_second": 125.0,
                        "logs_processed": 500,
                        "anomalies_found": 22,
                        "configured_fetch_limit": 500,
                        "configured_poll_interval_seconds": 30.0,
                        "configured_ceiling_logs_per_second": 16.667,
                        "cycles": 1,
                    },
                },
            },
            generated_at="2026-07-06T12:05:00+00:00",
        )

        assert "## Benchmark Config" in report
        assert "Replay lines: `5000`" in report
        assert "Rule engine status: `skipped`" in report
        assert "| ingester | 2026-07-06T12:00:00+00:00 | 2.500 | 400.000 | 1000 | 4 |" in report
        assert "Highest observed throughput: `ingester`" in report
        assert "Lowest observed throughput: `realtime_hybrid`" in report
        assert "Active processing throughput exceeds the configured steady-state ceiling" in report
        assert "Configured ceiling: `16.667`" in report
        assert "Measurement mode: `active_processing_no_sleep`" in report
