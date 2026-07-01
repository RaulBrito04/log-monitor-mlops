from __future__ import annotations

import json
import time

from src.monitoring import metrics


class TestRuntimeMetricsPersistence:
    def test_load_runtime_metrics_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(metrics, "RUNTIME_METRICS_FILE", tmp_path / "runtime_metrics.json")
        assert metrics._load_runtime_metrics() == {}

    def test_persist_runtime_metrics_merges_payload(self, tmp_path, monkeypatch):
        runtime_file = tmp_path / "runtime_metrics.json"
        monkeypatch.setattr(metrics, "RUNTIME_METRICS_FILE", runtime_file)
        runtime_file.write_text(json.dumps({"model": "baseline"}), encoding="utf-8")

        saved = metrics.persist_runtime_metrics({"ml_f1_score": 0.82, "dataset": "holdout"})

        assert saved["model"] == "baseline"
        assert saved["ml_f1_score"] == 0.82
        assert saved["dataset"] == "holdout"


class TestMonitoringRefresh:
    def test_refresh_monitoring_metrics_populates_gauges(self, mocker):
        metrics._LAST_REFRESH_TS = 0.0
        metrics.ALERTS_TOTAL.clear()
        metrics.ACTIVE_ALERTS.clear()
        metrics.ML_PREDICTIONS_TOTAL.clear()

        cursor = mocker.Mock()
        conn = mocker.Mock()
        conn.cursor.return_value = cursor

        mocker.patch("src.monitoring.metrics.psycopg2.connect", return_value=conn)
        mocker.patch(
            "src.monitoring.metrics._run_query",
            side_effect=[
                [("brute_force", "HIGH", 2), ("sql_injection", "CRITICAL", 1)],
                [("HIGH", 2), ("CRITICAL", 1)],
                [(25,)],
                [("anomaly", 5), ("normal", 20)],
                [(42.0,)],
            ],
        )
        mocker.patch(
            "src.monitoring.metrics._load_runtime_metrics",
            return_value={
                "ml_f1_score": 0.83,
                "model": "hybrid_ensemble",
                "dataset": "holdout",
                "retraining_baseline_available": 1,
                "retraining_candidate_available": 1,
                "retraining_promotable": 0,
                "retraining_feedback_events": 40,
                "retraining_ready_log_samples": 80,
                "retraining_reviewed_f1_delta": -0.03,
                "retraining_temporal_f1_delta": -0.02,
                "retraining_temporal_precision_delta": -0.01,
            },
        )
        info = mocker.patch.object(metrics.MONITORING_INFO, "info")

        metrics.refresh_monitoring_metrics(force=True)

        assert metrics.ALERTS_TOTAL.labels(alert_type="brute_force", severity="HIGH")._value.get() == 2
        assert metrics.LOGS_PROCESSED_TOTAL.labels(source="raw_logs")._value.get() == 25
        assert metrics.ML_MODEL_F1_SCORE.labels(model="hybrid_ensemble", dataset="holdout")._value.get() == 0.83
        assert metrics.ML_RETRAINING_BASELINE_AVAILABLE._value.get() == 1
        assert metrics.ML_RETRAINING_CANDIDATE_AVAILABLE._value.get() == 1
        assert metrics.ML_RETRAINING_PROMOTABLE._value.get() == 0
        assert metrics.ML_RETRAINING_ELIGIBLE_FEEDBACK_EVENTS._value.get() == 40
        assert metrics.ML_RETRAINING_READY_LOG_SAMPLES._value.get() == 80
        assert metrics.ML_RETRAINING_REVIEWED_F1_DELTA._value.get() == -0.03
        assert metrics.ML_RETRAINING_TEMPORAL_F1_DELTA._value.get() == -0.02
        assert metrics.ML_RETRAINING_TEMPORAL_PRECISION_DELTA._value.get() == -0.01
        assert metrics.DATA_FRESHNESS_SECONDS._value.get() == 42.0
        info.assert_called_once()
        conn.close.assert_called_once()

    def test_refresh_monitoring_metrics_honours_cache_window(self, mocker):
        metrics._LAST_REFRESH_TS = time.time()
        connect = mocker.patch("src.monitoring.metrics.psycopg2.connect")

        metrics.refresh_monitoring_metrics(force=False)

        connect.assert_not_called()
