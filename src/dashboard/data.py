from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import psycopg2
import requests
import streamlit as st
from psycopg2.extras import RealDictCursor

from src.dashboard.config import get_config
from src.ml.lime_explainer import LimeAlertExplainer, LimeUnavailableError

REQUEST_TIMEOUT_SECONDS = 5
DEFAULT_PAGE_LIMIT = 250


def _db_connection():
    config = get_config()
    return psycopg2.connect(
        host=config.postgres_host,
        port=config.postgres_port,
        dbname=config.postgres_db,
        user=config.postgres_user,
        password=config.postgres_password,
    )


def _rows(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with _db_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(sql, tuple(params))
            return [dict(row) for row in cursor.fetchall()]


def _one(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def _flask_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    config = get_config()
    response = requests.post(
        f"{config.flask_api_url}{path}",
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {}

    if response.status_code >= 400:
        message = response_payload.get("message") or response_payload.get("error") or f"HTTP {response.status_code}"
        raise ValueError(message)
    return response_payload


def _prometheus_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    config = get_config()
    response = requests.get(
        f"{config.prometheus_url}{path}",
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise ValueError(f"Prometheus query failed: {payload}")
    return payload


def _parse_vector_value(payload: dict[str, Any]) -> float | None:
    results = payload.get("data", {}).get("result", [])
    if not results:
        return None
    value = results[0].get("value", [None, None])[1]
    return float(value) if value is not None else None


def _range_to_frame(payload: dict[str, Any], value_label: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in payload.get("data", {}).get("result", []):
        metric = item.get("metric", {})
        series_label = metric.get("job") or metric.get("model") or metric.get("prediction") or "series"
        for ts, value in item.get("values", []):
            rows.append(
                {
                    "timestamp": datetime.fromtimestamp(float(ts), tz=timezone.utc),
                    value_label: float(value),
                    "series": series_label,
                }
            )
    return pd.DataFrame(rows)


def _fetch_targets() -> list[dict[str, Any]]:
    payload = _prometheus_get("/api/v1/targets")
    targets = payload.get("data", {}).get("activeTargets", [])
    result = []
    for target in targets:
        labels = target.get("labels", {})
        result.append(
            {
                "job": labels.get("job", "unknown"),
                "instance": labels.get("instance", "unknown"),
                "health": target.get("health", "unknown"),
                "last_error": target.get("lastError", ""),
                "scrape_url": target.get("scrapeUrl", ""),
            }
        )
    return result


def _fetch_overview_snapshot() -> dict[str, Any]:
    total_alerts = int(_one("SELECT COUNT(*) AS count FROM alerts")["count"])
    active_alerts = _rows(
        """
        SELECT severity, COUNT(*)::int AS count
        FROM alerts
        WHERE timestamp > NOW() - INTERVAL '24 hours'
        GROUP BY severity
        ORDER BY count DESC
        """
    )
    recent_anomalies = _rows(
        """
        SELECT date_trunc('hour', created_at) AS bucket, COUNT(*)::int AS count
        FROM hybrid_scores
        WHERE is_anomaly = TRUE
          AND created_at > NOW() - INTERVAL '24 hours'
        GROUP BY bucket
        ORDER BY bucket
        """
    )
    recent_alerts = _rows(
        """
        SELECT date_trunc('hour', timestamp) AS bucket, COUNT(*)::int AS count
        FROM alerts
        WHERE timestamp > NOW() - INTERVAL '24 hours'
        GROUP BY bucket
        ORDER BY bucket
        """
    )
    latest_log = _one("SELECT MAX(timestamp) AS timestamp FROM raw_logs")
    latest_f1 = _parse_vector_value(_prometheus_get("/api/v1/query", {"query": "logmonitor_ml_model_f1_score"}))
    data_freshness = _parse_vector_value(_prometheus_get("/api/v1/query", {"query": "logmonitor_data_freshness_seconds"}))
    return {
        "total_alerts": total_alerts,
        "active_alerts": active_alerts,
        "recent_anomalies": recent_anomalies,
        "recent_alerts": recent_alerts,
        "latest_log_timestamp": latest_log["timestamp"] if latest_log else None,
        "latest_f1": latest_f1,
        "data_freshness_seconds": data_freshness,
        "targets": _fetch_targets(),
    }


@st.cache_data(ttl=15, show_spinner=False)
def fetch_overview_snapshot() -> dict[str, Any]:
    return _fetch_overview_snapshot()


@st.cache_resource(show_spinner=False)
def get_lime_explainer() -> LimeAlertExplainer:
    return LimeAlertExplainer()


def _fetch_alert_options() -> dict[str, list[str]]:
    types = [row["alert_type"] for row in _rows("SELECT DISTINCT alert_type FROM alerts ORDER BY alert_type")]
    sources = [row["source"] for row in _rows("SELECT DISTINCT source FROM alerts ORDER BY source")]
    severities = [row["severity"] for row in _rows("SELECT DISTINCT severity FROM alerts ORDER BY severity")]
    statuses = [
        row["incident_status"]
        for row in _rows("SELECT DISTINCT COALESCE(incident_status, 'NEW') AS incident_status FROM alerts ORDER BY incident_status")
    ]
    return {"types": types, "sources": sources, "severities": severities, "statuses": statuses}


@st.cache_data(ttl=60, show_spinner=False)
def fetch_alert_options() -> dict[str, list[str]]:
    return _fetch_alert_options()


def _fetch_alerts(
    severity: str = "ALL",
    alert_type: str = "ALL",
    source: str = "ALL",
    incident_status: str = "ALL",
    ip_query: str = "",
    hours: int = 24,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = [
        """
        SELECT id, alert_type, severity, source, confidence, description,
               ip::text AS ip, timestamp, metadata, log_ids,
               COALESCE(incident_status, 'NEW') AS incident_status,
               COALESCE(incident_owner, '') AS incident_owner,
               incident_updated_at, incident_updated_by,
               COALESCE(array_length(log_ids, 1), 0) AS related_logs
        FROM alerts
        WHERE timestamp > NOW() - (%s * INTERVAL '1 hour')
        """
    ]
    params: list[Any] = [hours]
    if severity != "ALL":
        sql.append("AND severity = %s")
        params.append(severity)
    if alert_type != "ALL":
        sql.append("AND alert_type = %s")
        params.append(alert_type)
    if source != "ALL":
        sql.append("AND source = %s")
        params.append(source)
    if incident_status != "ALL":
        sql.append("AND COALESCE(incident_status, 'NEW') = %s")
        params.append(incident_status)
    if ip_query:
        sql.append("AND ip::text ILIKE %s")
        params.append(f"%{ip_query}%")
    sql.append("ORDER BY timestamp DESC LIMIT %s")
    params.append(limit)
    return _rows("\n".join(sql), params)


@st.cache_data(ttl=15, show_spinner=False)
def fetch_alerts(
    severity: str = "ALL",
    alert_type: str = "ALL",
    source: str = "ALL",
    incident_status: str = "ALL",
    ip_query: str = "",
    hours: int = 24,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return _fetch_alerts(severity, alert_type, source, incident_status, ip_query, hours, limit)


def _fetch_alert_detail(alert_id: int) -> dict[str, Any] | None:
    return _one(
        """
        SELECT id, alert_type, severity, source, confidence, description,
               ip::text AS ip, timestamp, metadata, log_ids, created_at,
               COALESCE(incident_status, 'NEW') AS incident_status,
               COALESCE(incident_owner, '') AS incident_owner,
               COALESCE(incident_notes, '') AS incident_notes,
               incident_updated_at, incident_updated_by
        FROM alerts
        WHERE id = %s
        """,
        (alert_id,),
    )


@st.cache_data(ttl=15, show_spinner=False)
def fetch_alert_detail(alert_id: int) -> dict[str, Any] | None:
    return _fetch_alert_detail(alert_id)


def _fetch_feedback_history(alert_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id, alert_id, user_id, label, reason, created_at
        FROM feedback
        WHERE alert_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT %s
        """,
        (alert_id, limit),
    )


@st.cache_data(ttl=15, show_spinner=False)
def fetch_feedback_history(alert_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return _fetch_feedback_history(alert_id, limit)


def _fetch_incident_history(alert_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id, alert_id, previous_status, new_status,
               COALESCE(previous_owner, '') AS previous_owner,
               COALESCE(new_owner, '') AS new_owner,
               COALESCE(change_notes, '') AS change_notes,
               COALESCE(changed_by, '') AS changed_by,
               changed_at
        FROM alert_incident_history
        WHERE alert_id = %s
        ORDER BY changed_at DESC, id DESC
        LIMIT %s
        """,
        (alert_id, limit),
    )


@st.cache_data(ttl=15, show_spinner=False)
def fetch_incident_history(alert_id: int, limit: int = 20) -> list[dict[str, Any]]:
    return _fetch_incident_history(alert_id, limit)


def submit_alert_feedback(alert_id: int, label: str, reason: str, user_id: str) -> dict[str, Any]:
    payload = _flask_post(
        "/api/alerts/feedback",
        {
            "alert_id": int(alert_id),
            "label": label,
            "reason": reason,
            "user_id": user_id,
        },
    )
    clear_dashboard_caches()
    return payload


def submit_alert_incident(
    alert_id: int,
    incident_status: str,
    incident_owner: str,
    incident_notes: str,
    user_id: str,
) -> dict[str, Any]:
    payload = _flask_post(
        "/api/alerts/incident",
        {
            "alert_id": int(alert_id),
            "incident_status": incident_status,
            "incident_owner": incident_owner,
            "incident_notes": incident_notes,
            "user_id": user_id,
        },
    )
    clear_dashboard_caches()
    return payload


def _fetch_logs_for_ids(log_ids: tuple[int, ...], limit: int = 50) -> list[dict[str, Any]]:
    if not log_ids:
        return []
    return _rows(
        """
        SELECT id, timestamp, ip::text AS ip, method, endpoint, status,
               response_time_ms, data
        FROM raw_logs
        WHERE id = ANY(%s)
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        (list(log_ids), limit),
    )


@st.cache_data(ttl=15, show_spinner=False)
def fetch_logs_for_ids(log_ids: tuple[int, ...], limit: int = 50) -> list[dict[str, Any]]:
    return _fetch_logs_for_ids(log_ids, limit)


def _fetch_logs(
    hours: int = 24,
    ip_query: str = "",
    endpoint_query: str = "",
    method: str = "ALL",
    status_code: int | None = None,
    search_query: str = "",
    limit: int = DEFAULT_PAGE_LIMIT,
) -> list[dict[str, Any]]:
    sql = [
        """
        SELECT id, timestamp, ip::text AS ip, method, endpoint, status,
               response_time_ms, user_agent, data
        FROM raw_logs
        WHERE timestamp > NOW() - (%s * INTERVAL '1 hour')
        """
    ]
    params: list[Any] = [hours]
    if ip_query:
        sql.append("AND ip::text ILIKE %s")
        params.append(f"%{ip_query}%")
    if endpoint_query:
        sql.append("AND endpoint ILIKE %s")
        params.append(f"%{endpoint_query}%")
    if method != "ALL":
        sql.append("AND method = %s")
        params.append(method)
    if status_code is not None:
        sql.append("AND status = %s")
        params.append(status_code)
    if search_query:
        sql.append("AND (endpoint ILIKE %s OR CAST(data AS TEXT) ILIKE %s)")
        params.extend([f"%{search_query}%", f"%{search_query}%"])
    sql.append("ORDER BY timestamp DESC LIMIT %s")
    params.append(limit)
    return _rows("\n".join(sql), params)


@st.cache_data(ttl=15, show_spinner=False)
def fetch_logs(
    hours: int = 24,
    ip_query: str = "",
    endpoint_query: str = "",
    method: str = "ALL",
    status_code: int | None = None,
    search_query: str = "",
    limit: int = DEFAULT_PAGE_LIMIT,
) -> list[dict[str, Any]]:
    return _fetch_logs(hours, ip_query, endpoint_query, method, status_code, search_query, limit)


def _fetch_log_context(log_id: int) -> dict[str, Any]:
    hybrid_score = _one(
        """
        SELECT log_id, rule_score, ml_score, final_score, severity, triggered_rules,
               ml_confidence, is_anomaly, created_at
        FROM hybrid_scores
        WHERE log_id = %s
        """,
        (log_id,),
    )
    related_alerts = _rows(
        """
        SELECT id, alert_type, severity, source, description, timestamp,
               ip::text AS ip, confidence
        FROM alerts
        WHERE %s = ANY(log_ids)
        ORDER BY timestamp DESC
        LIMIT 25
        """,
        (log_id,),
    )
    return {"hybrid_score": hybrid_score, "related_alerts": related_alerts}


@st.cache_data(ttl=15, show_spinner=False)
def fetch_log_context(log_id: int) -> dict[str, Any]:
    return _fetch_log_context(log_id)


def _fetch_prediction_split() -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT CASE WHEN is_anomaly THEN 'anomaly' ELSE 'normal' END AS prediction,
               COUNT(*)::int AS count
        FROM hybrid_scores
        GROUP BY prediction
        ORDER BY prediction
        """
    )


@st.cache_data(ttl=15, show_spinner=False)
def fetch_prediction_split() -> list[dict[str, Any]]:
    return _fetch_prediction_split()


def _fetch_alert_trend(hours: int = 24) -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT date_trunc('hour', timestamp) AS bucket, COUNT(*)::int AS count
        FROM alerts
        WHERE timestamp > NOW() - (%s * INTERVAL '1 hour')
        GROUP BY bucket
        ORDER BY bucket
        """,
        (hours,),
    )


@st.cache_data(ttl=15, show_spinner=False)
def fetch_alert_trend(hours: int = 24) -> list[dict[str, Any]]:
    return _fetch_alert_trend(hours)


def _fetch_ml_f1_history(hours: int = 24, step: str = "5m") -> pd.DataFrame:
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=hours)
    payload = _prometheus_get(
        "/api/v1/query_range",
        {
            "query": "logmonitor_ml_model_f1_score",
            "start": start_time.timestamp(),
            "end": end_time.timestamp(),
            "step": step,
        },
    )
    return _range_to_frame(payload, "f1_score")


@st.cache_data(ttl=15, show_spinner=False)
def fetch_ml_f1_history(hours: int = 24, step: str = "5m") -> pd.DataFrame:
    return _fetch_ml_f1_history(hours, step)


def _fetch_log_context_window(log_id: int, limit: int = 250) -> list[dict[str, Any]]:
    target_log = _one(
        """
        SELECT id, timestamp, ip::text AS ip
        FROM raw_logs
        WHERE id = %s
        """,
        (log_id,),
    )
    if not target_log:
        return []

    return _rows(
        """
        SELECT id, timestamp, ip::text AS ip, method, endpoint, status,
               response_time_ms, user_agent, data
        FROM (
            SELECT id, timestamp, ip, method, endpoint, status,
                   response_time_ms, user_agent, data
            FROM raw_logs
            WHERE timestamp <= %s
              AND timestamp >= %s - INTERVAL '60 minutes'
            ORDER BY timestamp DESC, id DESC
            LIMIT %s
        ) recent_logs
        ORDER BY timestamp, id
        """,
        (target_log["timestamp"], target_log["timestamp"], limit),
    )



def _fetch_alert_explanation(alert_id: int, log_id: int | None = None, top_k: int = 8) -> dict[str, Any]:
    detail = _fetch_alert_detail(alert_id)
    if not detail:
        return {"status": "unavailable", "message": f"Alert {alert_id} could not be loaded."}

    available_log_ids = [int(value) for value in (detail.get("log_ids") or [])]
    if not available_log_ids:
        return {"status": "unavailable", "message": "No related log IDs are attached to this alert."}

    target_log_id = int(log_id or available_log_ids[0])
    if target_log_id not in available_log_ids:
        return {
            "status": "unavailable",
            "message": f"Log {target_log_id} is not attached to alert {alert_id}.",
        }

    context_rows = _fetch_log_context_window(target_log_id)
    raw_context = pd.DataFrame(context_rows) if context_rows else pd.DataFrame()

    try:
        explanation = get_lime_explainer().explain_log(
            target_log_id,
            raw_log_context=raw_context if not raw_context.empty else None,
            top_k=top_k,
        )
    except (LimeUnavailableError, LookupError, FileNotFoundError, ValueError) as exc:
        return {"status": "unavailable", "message": str(exc), "log_id": target_log_id}

    return {
        "status": "ok",
        "alert_id": int(alert_id),
        "available_log_ids": available_log_ids,
        "context_rows": int(len(raw_context)),
        **explanation,
    }


@st.cache_data(ttl=30, show_spinner=False)
def fetch_alert_explanation(alert_id: int, log_id: int | None = None, top_k: int = 8) -> dict[str, Any]:
    return _fetch_alert_explanation(alert_id, log_id, top_k)


def clear_dashboard_caches() -> None:
    fetch_overview_snapshot.clear()
    fetch_alert_options.clear()
    fetch_alerts.clear()
    fetch_alert_detail.clear()
    fetch_feedback_history.clear()
    fetch_incident_history.clear()
    fetch_logs_for_ids.clear()
    fetch_logs.clear()
    fetch_log_context.clear()
    fetch_prediction_split.clear()
    fetch_alert_trend.clear()
    fetch_ml_f1_history.clear()
    fetch_alert_explanation.clear()
    get_lime_explainer.clear()

