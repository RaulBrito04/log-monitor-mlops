from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg2
from prometheus_client import Gauge, Histogram, Info

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_METRICS_FILE = PROJECT_ROOT / "data" / "runtime_metrics.json"

DB_QUERY_DURATION = Histogram(
    "logmonitor_db_query_duration_seconds",
    "Database query duration for monitoring collectors",
    ["query_type"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 2.5),
)

ALERTS_TOTAL = Gauge(
    "logmonitor_alerts_total",
    "Total alerts stored in PostgreSQL",
    ["alert_type", "severity"],
)

ACTIVE_ALERTS = Gauge(
    "logmonitor_active_alerts",
    "Alerts created in the last 24 hours",
    ["severity"],
)

ML_PREDICTIONS_TOTAL = Gauge(
    "logmonitor_ml_predictions_total",
    "Total hybrid predictions stored in PostgreSQL",
    ["prediction"],
)

ML_MODEL_F1_SCORE = Gauge(
    "logmonitor_ml_model_f1_score",
    "Tracked ML model F1 score",
    ["model", "dataset"],
)

ML_RETRAINING_BASELINE_AVAILABLE = Gauge(
    "logmonitor_ml_retraining_baseline_available",
    "Whether a current baseline model was available for the latest reviewed-feedback retraining run",
)

ML_RETRAINING_CANDIDATE_AVAILABLE = Gauge(
    "logmonitor_ml_retraining_candidate_available",
    "Whether the latest reviewed-feedback retraining run produced a candidate model",
)

ML_RETRAINING_PROMOTABLE = Gauge(
    "logmonitor_ml_retraining_promotable",
    "Whether the latest reviewed-feedback candidate satisfied the promotion gate",
)

ML_RETRAINING_ELIGIBLE_FEEDBACK_EVENTS = Gauge(
    "logmonitor_ml_retraining_feedback_events",
    "Eligible feedback events used by the latest reviewed-feedback retraining run",
)

ML_RETRAINING_READY_LOG_SAMPLES = Gauge(
    "logmonitor_ml_retraining_ready_log_samples",
    "Training-ready reviewed log samples in the latest reviewed-feedback retraining run",
)

ML_RETRAINING_REVIEWED_F1_DELTA = Gauge(
    "logmonitor_ml_retraining_reviewed_f1_delta",
    "Reviewed holdout F1 delta between the latest candidate and current baseline",
)

ML_RETRAINING_TEMPORAL_F1_DELTA = Gauge(
    "logmonitor_ml_retraining_temporal_f1_delta",
    "Temporal holdout F1 delta between the latest candidate and current baseline",
)

ML_RETRAINING_TEMPORAL_PRECISION_DELTA = Gauge(
    "logmonitor_ml_retraining_temporal_precision_delta",
    "Temporal holdout precision delta between the latest candidate and current baseline",
)

LOGS_PROCESSED_TOTAL = Gauge(
    "logmonitor_logs_processed_total",
    "Total processed logs stored in PostgreSQL",
    ["source"],
)

DATA_FRESHNESS_SECONDS = Gauge(
    "logmonitor_data_freshness_seconds",
    "Seconds since the latest raw log timestamp",
)

MONITORING_INFO = Info("logmonitor_app", "Application information")

_LAST_REFRESH_TS = 0.0
_REFRESH_INTERVAL_SECONDS = 15.0


def _db_config() -> dict[str, Any]:
    return {
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "database": os.getenv("POSTGRES_DB", "logmonitor"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", "changeme"),
    }


def _run_query(cursor, query_type: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    start = time.perf_counter()
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    DB_QUERY_DURATION.labels(query_type=query_type).observe(time.perf_counter() - start)
    return rows


def _load_runtime_metrics() -> dict[str, Any]:
    if not RUNTIME_METRICS_FILE.exists():
        return {}
    try:
        return json.loads(RUNTIME_METRICS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def persist_runtime_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    RUNTIME_METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    current = _load_runtime_metrics()
    current.update(payload)
    RUNTIME_METRICS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def refresh_monitoring_metrics(force: bool = False) -> None:
    global _LAST_REFRESH_TS
    now = time.time()
    if not force and (now - _LAST_REFRESH_TS) < _REFRESH_INTERVAL_SECONDS:
        return

    ALERTS_TOTAL.clear()
    ACTIVE_ALERTS.clear()
    ML_PREDICTIONS_TOTAL.clear()

    try:
        conn = psycopg2.connect(**_db_config())
        try:
            cursor = conn.cursor()

            for alert_type, severity, count in _run_query(
                cursor,
                "alerts_total",
                """
                SELECT alert_type, severity, COUNT(*)
                FROM alerts
                GROUP BY alert_type, severity
                """,
            ):
                ALERTS_TOTAL.labels(alert_type=alert_type, severity=severity).set(count)

            for severity, count in _run_query(
                cursor,
                "active_alerts",
                """
                SELECT severity, COUNT(*)
                FROM alerts
                WHERE timestamp > NOW() - INTERVAL '24 hours'
                GROUP BY severity
                """,
            ):
                ACTIVE_ALERTS.labels(severity=severity).set(count)

            log_count = _run_query(
                cursor,
                "logs_processed",
                "SELECT COUNT(*) FROM raw_logs",
            )[0][0]
            LOGS_PROCESSED_TOTAL.labels(source="raw_logs").set(log_count)

            for prediction, count in _run_query(
                cursor,
                "ml_predictions",
                """
                SELECT CASE WHEN is_anomaly THEN 'anomaly' ELSE 'normal' END AS prediction, COUNT(*)
                FROM hybrid_scores
                GROUP BY prediction
                """,
            ):
                ML_PREDICTIONS_TOTAL.labels(prediction=prediction).set(count)

            freshness_rows = _run_query(
                cursor,
                "data_freshness",
                """
                SELECT EXTRACT(EPOCH FROM (NOW() - MAX(timestamp)))
                FROM raw_logs
                """,
            )
            freshness = freshness_rows[0][0]
            DATA_FRESHNESS_SECONDS.set(float(freshness) if freshness is not None else 0.0)
        finally:
            conn.close()
    except Exception:
        _LAST_REFRESH_TS = now
        return

    runtime_metrics = _load_runtime_metrics()
    f1_value = float(runtime_metrics.get("ml_f1_score", 0.0) or 0.0)
    model_name = str(runtime_metrics.get("model", "hybrid_ensemble"))
    dataset_name = str(runtime_metrics.get("dataset", "holdout"))
    ML_MODEL_F1_SCORE.labels(model=model_name, dataset=dataset_name).set(f1_value)
    ML_RETRAINING_BASELINE_AVAILABLE.set(float(runtime_metrics.get("retraining_baseline_available", 0.0) or 0.0))
    ML_RETRAINING_CANDIDATE_AVAILABLE.set(float(runtime_metrics.get("retraining_candidate_available", 0.0) or 0.0))
    ML_RETRAINING_PROMOTABLE.set(float(runtime_metrics.get("retraining_promotable", 0.0) or 0.0))
    ML_RETRAINING_ELIGIBLE_FEEDBACK_EVENTS.set(float(runtime_metrics.get("retraining_feedback_events", 0.0) or 0.0))
    ML_RETRAINING_READY_LOG_SAMPLES.set(float(runtime_metrics.get("retraining_ready_log_samples", 0.0) or 0.0))
    ML_RETRAINING_REVIEWED_F1_DELTA.set(float(runtime_metrics.get("retraining_reviewed_f1_delta", 0.0) or 0.0))
    ML_RETRAINING_TEMPORAL_F1_DELTA.set(float(runtime_metrics.get("retraining_temporal_f1_delta", 0.0) or 0.0))
    ML_RETRAINING_TEMPORAL_PRECISION_DELTA.set(
        float(runtime_metrics.get("retraining_temporal_precision_delta", 0.0) or 0.0)
    )

    MONITORING_INFO.info(
        {
            "version": os.getenv("LOGMONITOR_VERSION", "0.10.0"),
            "environment": os.getenv("LOGMONITOR_ENV", "development"),
            "ml_model": model_name,
        }
    )

    _LAST_REFRESH_TS = now
