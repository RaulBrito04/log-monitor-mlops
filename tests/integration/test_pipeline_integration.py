from __future__ import annotations

import psycopg2
import pytest

from src.detection import rule_engine
from src.log_processor import ingester
from src.ml.hybrid_pipeline import HybridPipeline


@pytest.mark.integration
def test_ingester_and_rule_engine_flow(test_db, clean_db, sample_logs, anomalous_logs):
    conn = psycopg2.connect(**test_db)
    cursor = conn.cursor()

    inserted = ingester.insert_logs_batch(cursor, sample_logs + anomalous_logs)
    conn.commit()

    assert inserted == len(sample_logs) + len(anomalous_logs)

    summary = rule_engine.run_once(cursor, "365 days", "TEST")
    conn.commit()

    cursor.execute("SELECT alert_type, COUNT(*) FROM alerts GROUP BY alert_type")
    grouped = dict(cursor.fetchall())

    assert summary["alerts_created"] >= 2
    assert grouped.get("brute_force", 0) >= 1
    assert grouped.get("sql_injection", 0) >= 1

    cursor.close()
    conn.close()


@pytest.mark.integration
def test_hybrid_pipeline_persists_scores_to_test_db(test_db, clean_db, monkeypatch, mocker, mock_model, mock_scaler, tmp_path):
    conn = psycopg2.connect(**test_db)
    cursor = conn.cursor()

    inserted = ingester.insert_logs_batch(
        cursor,
        [
            {
                "log_type": "web",
                "timestamp": "2026-04-15T12:00:00+00:00",
                "ip": "10.0.0.10",
                "method": "POST",
                "endpoint": "/login",
                "status": 401,
                "response_time_ms": 75,
                "user_agent": "pytest",
            }
        ],
    )
    conn.commit()
    assert inserted == 1

    cursor.execute("SELECT id FROM raw_logs ORDER BY id LIMIT 1")
    log_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO alerts (alert_type, severity, source, confidence, description, log_ids, ip, timestamp, metadata)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s::jsonb)
        """,
        (
            "brute_force",
            "HIGH",
            "rule",
            1.0,
            "Test alert",
            [int(log_id)],
            "10.0.0.10",
            "{}",
        ),
    )
    conn.commit()
    cursor.close()
    conn.close()

    monkeypatch.setenv("POSTGRES_HOST", str(test_db["host"]))
    monkeypatch.setenv("POSTGRES_PORT", str(test_db["port"]))
    monkeypatch.setenv("POSTGRES_DB", str(test_db["database"]))
    monkeypatch.setenv("POSTGRES_USER", str(test_db["user"]))
    monkeypatch.setenv("POSTGRES_PASSWORD", str(test_db["password"]))

    features_path = tmp_path / "features.txt"
    features_path.write_text("status_code\nresponse_time_ms\nendpoint_entropy\n", encoding="utf-8")

    mock_model.decision_function.return_value = [-0.2]
    mock_scaler.transform.side_effect = lambda X: X
    bundle = {"model": mock_model, "scaler": mock_scaler}
    mocker.patch("src.ml.hybrid_pipeline._safe_load_pickle", return_value=bundle)

    pipeline = HybridPipeline(
        model_path="models/model.pkl",
        scaler_path="models/scaler.pkl",
        features_path=str(features_path),
    )

    result = pipeline.evaluate_log(
        int(log_id),
        {"status_code": 401.0, "response_time_ms": 75.0, "endpoint_entropy": 1.5},
    )

    verify_conn = psycopg2.connect(**test_db)
    verify_cursor = verify_conn.cursor()
    verify_cursor.execute(
        "SELECT log_id, severity, is_anomaly FROM hybrid_scores WHERE log_id = %s",
        (int(log_id),),
    )
    saved = verify_cursor.fetchone()

    assert result["log_id"] == int(log_id)
    assert saved[0] == int(log_id)
    assert saved[1] in {"MEDIUM", "HIGH", "CRITICAL"}
    assert saved[2] is True

    verify_cursor.close()
    verify_conn.close()
    pipeline.conn.close()

