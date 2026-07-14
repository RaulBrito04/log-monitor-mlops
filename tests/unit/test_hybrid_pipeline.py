from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.ml.hybrid_pipeline import HybridPipeline


@pytest.fixture
def pipeline(tmp_path, mocker, mock_model, mock_scaler):
    features_path = tmp_path / "features.txt"
    features_path.write_text("status_code\nresponse_time_ms\nendpoint_entropy\n", encoding="utf-8")

    cursor = mocker.Mock()
    cursor.fetchall.return_value = []
    conn = mocker.Mock()
    conn.cursor.return_value = cursor

    bundle = {"model": mock_model, "scaler": mock_scaler}
    mocker.patch("src.ml.hybrid_pipeline._safe_load_pickle", return_value=bundle)
    mocker.patch("src.ml.hybrid_pipeline.psycopg2.connect", return_value=conn)

    return HybridPipeline(
        model_path="models/model.pkl",
        scaler_path="models/scaler.pkl",
        features_path=str(features_path),
    )


class TestHybridPipelineScoring:
    def test_weights_sum_validation(self, tmp_path, mocker):
        features_path = tmp_path / "features.txt"
        features_path.write_text("feature1\n", encoding="utf-8")

        bundle = {"model": MagicMock(), "scaler": MagicMock()}
        mocker.patch("src.ml.hybrid_pipeline._safe_load_pickle", return_value=bundle)
        mocker.patch("src.ml.hybrid_pipeline.psycopg2.connect", return_value=mocker.Mock())

        with pytest.raises(ValueError):
            HybridPipeline(
                model_path="models/model.pkl",
                scaler_path="models/scaler.pkl",
                features_path=str(features_path),
                rule_weight=0.6,
                ml_weight=0.6,
            )

    def test_get_rule_score_prefers_highest_severity(self, pipeline):
        cursor = pipeline.conn.cursor.return_value
        cursor.fetchall.return_value = [
            (123, "port_scanning", "LOW"),
            (123, "sql_injection", "CRITICAL"),
        ]

        score, rule_ids = pipeline.get_rule_score(123)

        assert score == 1.0
        assert rule_ids == ["port_scanning", "sql_injection"]

    def test_get_rule_scores_batch_returns_defaults_for_missing_logs(self, pipeline):
        cursor = pipeline.conn.cursor.return_value
        cursor.fetchall.return_value = [(2, "brute_force", "HIGH")]

        scores = pipeline.get_rule_scores_batch([1, 2])

        assert scores[1] == (0.0, [])
        assert scores[2] == (0.75, ["brute_force"])

    def test_get_ml_score_clamps_to_expected_range(self, pipeline, mock_model):
        mock_model.decision_function.return_value = [-1.0]

        score, confidence = pipeline.get_ml_score(
            {"status_code": 401.0, "response_time_ms": 50.0, "endpoint_entropy": 1.2}
        )

        assert 0.99 <= score <= 1.0
        assert 0.0 <= confidence <= 1.0

    def test_get_ml_scores_batch_replaces_nan_features_before_inference(self, pipeline, mock_scaler, mock_model):
        mock_model.decision_function.return_value = [-0.1, 0.0]
        features_df = pd.DataFrame(
            [
                {"status_code": 401.0, "response_time_ms": float("nan"), "endpoint_entropy": 1.2},
                {"status_code": 200.0, "response_time_ms": 25.0, "endpoint_entropy": 0.2},
            ]
        )

        scores_df = pipeline.get_ml_scores_batch(features_df)

        transformed_frame = mock_scaler.transform.call_args[0][0]
        assert transformed_frame.iloc[0]["response_time_ms"] == 0.0
        assert list(scores_df.columns) == ["ml_score", "ml_confidence"]
        assert len(scores_df) == 2

    def test_combine_scores_applies_critical_override(self, pipeline):
        result = pipeline.combine_scores(rule_score=1.0, ml_score=0.0)
        assert result >= 0.75

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.95, "CRITICAL"),
            (0.80, "CRITICAL"),
            (0.79, "HIGH"),
            (0.60, "HIGH"),
            (0.59, "MEDIUM"),
            (0.40, "MEDIUM"),
            (0.39, "NORMAL"),
        ],
    )
    def test_classify_severity_thresholds(self, pipeline, score, expected):
        assert pipeline.classify_severity(score) == expected

    def test_evaluate_log_returns_expected_payload(self, pipeline, mocker):
        persist_mock = mocker.patch.object(pipeline, "_persist")
        mocker.patch.object(pipeline, "get_rule_score", return_value=(0.75, ["brute_force"]))
        mocker.patch.object(pipeline, "get_ml_score", return_value=(0.50, 0.40))

        result = pipeline.evaluate_log(
            99,
            {"status_code": 401.0, "response_time_ms": 50.0, "endpoint_entropy": 1.1},
        )

        assert result["log_id"] == 99
        assert result["severity"] == "HIGH"
        assert result["is_anomaly"] is True
        persist_mock.assert_called_once_with(result)

    def test_evaluate_log_can_skip_persist(self, pipeline, mocker):
        persist_mock = mocker.patch.object(pipeline, "_persist")
        mocker.patch.object(pipeline, "get_rule_score", return_value=(0.0, []))
        mocker.patch.object(pipeline, "get_ml_score", return_value=(0.10, 0.80))

        result = pipeline.evaluate_log(
            101,
            {"status_code": 200.0, "response_time_ms": 20.0, "endpoint_entropy": 0.2},
            persist=False,
        )

        assert result["log_id"] == 101
        assert result["is_anomaly"] is False
        persist_mock.assert_not_called()

    def test_persist_inserts_row(self, pipeline, mocker):
        execute_values = mocker.patch("src.ml.hybrid_pipeline.execute_values")
        result = {
            "log_id": 5,
            "rule_score": 0.75,
            "ml_score": 0.25,
            "final_score": 0.525,
            "severity": "MEDIUM",
            "triggered_rules": ["brute_force"],
            "ml_confidence": 0.5,
            "is_anomaly": True,
        }

        pipeline._persist(result)

        cursor = pipeline.conn.cursor.return_value
        execute_values.assert_called_once()
        assert execute_values.call_args.args[0] == cursor
        assert execute_values.call_args.args[2] == [
            (5, 0.75, 0.25, 0.525, "MEDIUM", ["brute_force"], 0.5, True)
        ]
        pipeline.conn.commit.assert_called_once()

    def test_evaluate_batch_persists_once_for_full_batch(self, pipeline, mocker):
        persist_batch = mocker.patch.object(pipeline, "_persist_batch")
        mocker.patch.object(
            pipeline,
            "get_rule_scores_batch",
            return_value={1: (0.0, []), 2: (0.75, ["brute_force"])},
        )
        mocker.patch.object(
            pipeline,
            "get_ml_scores_batch",
            return_value=pd.DataFrame(
                {
                    "ml_score": [0.10, 0.50],
                    "ml_confidence": [0.80, 0.40],
                }
            ),
        )
        logs_df = pd.DataFrame(
            [
                {"log_id": 1, "status_code": 200.0, "response_time_ms": 20.0, "endpoint_entropy": 0.2},
                {"log_id": 2, "status_code": 401.0, "response_time_ms": 50.0, "endpoint_entropy": 1.1},
            ]
        )

        results_df = pipeline.evaluate_batch(logs_df, verbose=False)

        persist_batch.assert_called_once()
        persisted_results = persist_batch.call_args.args[0]
        assert len(persisted_results) == 2
        assert list(results_df["log_id"]) == [1, 2]
        assert pipeline.last_batch_summary["logs_processed"] == 2
