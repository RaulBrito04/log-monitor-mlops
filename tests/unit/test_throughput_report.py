from __future__ import annotations

from src.monitoring.throughput_report import build_baseline_report


class TestThroughputReport:
    def test_build_baseline_report_handles_missing_snapshots(self):
        report = build_baseline_report({})

        assert "No throughput component snapshots were found" in report
        assert "generate_throughput_baseline_report.py" in report

    def test_build_baseline_report_renders_component_summary(self):
        report = build_baseline_report(
            {
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
                    },
                }
            },
            generated_at="2026-07-06T12:05:00+00:00",
        )

        assert "| ingester | 2026-07-06T12:00:00+00:00 | 2.500 | 400.000 | 1000 | 4 |" in report
        assert "Highest observed throughput: `ingester`" in report
        assert "Lowest observed throughput: `realtime_hybrid`" in report
        assert "## realtime_hybrid" in report
