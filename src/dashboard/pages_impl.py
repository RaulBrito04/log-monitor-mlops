from __future__ import annotations

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from src.dashboard import data
from src.dashboard.auth import current_user
from src.dashboard.config import get_config
from src.dashboard.playbooks import get_playbook
from src.dashboard.ui import format_timestamp, humanize_seconds, render_empty, render_error, render_sidebar


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _maybe_autorefresh(page_key: str, enabled: bool) -> None:
    if enabled:
        config = get_config()
        st_autorefresh(interval=config.refresh_seconds * 1000, key=page_key)


def render_overview_page() -> None:
    render_sidebar("Overview", auto_refresh=True)
    _maybe_autorefresh("overview-refresh", True)

    try:
        snapshot = data.fetch_overview_snapshot()
    except Exception as exc:
        render_error(f"Failed to load overview data: {exc}")
        return

    active_total = sum(item.get("count", 0) for item in snapshot.get("active_alerts", []))
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total alerts", snapshot.get("total_alerts", 0))
    col2.metric("Active alerts (24h)", active_total)
    col3.metric("Current ML F1", f"{(snapshot.get('latest_f1') or 0):.3f}")
    col4.metric("Data freshness", humanize_seconds(snapshot.get("data_freshness_seconds")))

    st.markdown("<p class='dashboard-note'>Overview combines Prometheus live metrics with PostgreSQL investigation context.</p>", unsafe_allow_html=True)

    svc_col, severity_col = st.columns((1.2, 1))
    with svc_col:
        st.subheader("Observed services")
        targets_frame = _to_frame(snapshot.get("targets", []))
        if targets_frame.empty:
            render_empty("No Prometheus targets were returned.")
        else:
            st.dataframe(targets_frame, use_container_width=True, hide_index=True)

    with severity_col:
        st.subheader("Active alerts by severity")
        severity_frame = _to_frame(snapshot.get("active_alerts", []))
        if severity_frame.empty:
            render_empty("No active alerts were found in the last 24 hours.")
        else:
            st.bar_chart(severity_frame.set_index("severity"))

    trend_col, anomaly_col = st.columns(2)
    with trend_col:
        st.subheader("Alert trend (24h)")
        alert_frame = _to_frame(snapshot.get("recent_alerts", []))
        if alert_frame.empty:
            render_empty("No recent alert trend is available.")
        else:
            st.line_chart(alert_frame.set_index("bucket")["count"])

    with anomaly_col:
        st.subheader("Anomaly volume (24h)")
        anomaly_frame = _to_frame(snapshot.get("recent_anomalies", []))
        if anomaly_frame.empty:
            render_empty("No anomaly volume is available yet.")
        else:
            st.area_chart(anomaly_frame.set_index("bucket")["count"])

    st.subheader("Latest log seen")
    st.write(format_timestamp(snapshot.get("latest_log_timestamp")))


def render_alerts_page() -> None:
    render_sidebar("Alerts", auto_refresh=True)
    _maybe_autorefresh("alerts-refresh", True)

    try:
        options = data.fetch_alert_options()
    except Exception as exc:
        render_error(f"Failed to load alert filter options: {exc}")
        return

    f1, f2, f3, f4, f5, f6 = st.columns(6)
    severity = f1.selectbox("Severity", ["ALL", *options.get("severities", [])])
    alert_type = f2.selectbox("Alert type", ["ALL", *options.get("types", [])])
    source = f3.selectbox("Source", ["ALL", *options.get("sources", [])])
    incident_status = f4.selectbox("Incident status", ["ALL", *options.get("statuses", [])])
    hours = f5.selectbox("Time window", [1, 6, 24, 72], index=2)
    ip_query = f6.text_input("IP contains")

    try:
        alerts = data.fetch_alerts(severity, alert_type, source, incident_status, ip_query, int(hours))
    except Exception as exc:
        render_error(f"Failed to load alerts: {exc}")
        return

    alerts_frame = _to_frame(alerts)
    st.subheader("Alert queue")
    if alerts_frame.empty:
        render_empty("No alerts matched the selected filters.")
        return

    st.dataframe(
        alerts_frame[["id", "alert_type", "severity", "source", "incident_status", "incident_owner", "ip", "timestamp", "related_logs", "confidence"]],
        use_container_width=True,
        hide_index=True,
    )

    selected_alert_id = st.selectbox(
        "Alert detail",
        options=alerts_frame["id"].tolist(),
        format_func=lambda value: f"Alert #{value} - {alerts_frame.loc[alerts_frame['id'] == value, 'alert_type'].iloc[0]}",
    )

    try:
        detail = data.fetch_alert_detail(int(selected_alert_id))
    except Exception as exc:
        render_error(f"Failed to load selected alert: {exc}")
        return

    if not detail:
        render_empty("The selected alert could not be loaded.")
        return

    playbook = get_playbook(detail["alert_type"])
    detail_col, playbook_col = st.columns((1.2, 1))

    with detail_col:
        st.subheader(f"Alert #{detail['id']}")
        st.write(detail["description"])
        st.json(
            {
                "severity": detail["severity"],
                "source": detail["source"],
                "confidence": detail["confidence"],
                "ip": detail["ip"],
                "timestamp": format_timestamp(detail["timestamp"]),
                "incident_status": detail.get("incident_status") or "NEW",
                "incident_owner": detail.get("incident_owner") or "",
                "incident_notes": detail.get("incident_notes") or "",
                "incident_updated_at": format_timestamp(detail.get("incident_updated_at")),
                "incident_updated_by": detail.get("incident_updated_by") or "",
                "related_log_ids": detail.get("log_ids") or [],
                "metadata": detail.get("metadata") or {},
            },
            expanded=False,
        )
        related_logs = data.fetch_logs_for_ids(tuple(detail.get("log_ids") or ()))
        if related_logs:
            st.subheader("Related logs")
            st.dataframe(_to_frame(related_logs), use_container_width=True, hide_index=True)

        available_log_ids = detail.get("log_ids") or []
        st.subheader("Alert-level explainability")
        if not available_log_ids:
            render_empty("No related logs are available for local explainability.")
        else:
            explanation_log_id = st.selectbox(
                "Explain related log",
                options=available_log_ids,
                key=f"explain-log-{detail['id']}",
                format_func=lambda value: f"Log #{value}",
            )

            st.caption("Local explainability currently targets the supervised Random Forest view of this alert context.")
            st.subheader("LIME explanation")
            explanation = data.fetch_alert_explanation(int(detail["id"]), int(explanation_log_id))
            if explanation.get("status") != "ok":
                render_empty(explanation.get("message", "LIME explanation is unavailable for this alert."))
            else:
                exp1, exp2, exp3 = st.columns(3)
                exp1.metric("RF anomaly probability", f"{explanation['anomaly_probability']:.3f}")
                exp2.metric("Explained log", explanation["log_id"])
                exp3.metric("Context rows", explanation.get("context_rows", 0))
                st.caption(
                    f"Model: {explanation['model_family']} | "
                    f"Feature source: {explanation['feature_source']} | "
                    "Explanation target: anomaly class"
                )
                explanation_frame = _to_frame(explanation.get("top_features", []))
                if explanation_frame.empty:
                    render_empty("LIME did not return any local feature contributions for this alert.")
                else:
                    st.dataframe(
                        explanation_frame[["rank", "feature", "value", "weight", "direction", "rule"]],
                        use_container_width=True,
                        hide_index=True,
                    )

            st.subheader("Counterfactual explanation")
            counterfactual = data.fetch_alert_counterfactual(int(detail["id"]), int(explanation_log_id))
            if counterfactual.get("status") != "ok":
                render_empty(counterfactual.get("message", "Counterfactual explanation is unavailable for this alert."))
            else:
                cf1, cf2, cf3 = st.columns(3)
                cf1.metric("Current anomaly prob.", f"{counterfactual['anomaly_probability']:.3f}")
                cf2.metric("Counterfactual prob.", f"{counterfactual['counterfactual_anomaly_probability']:.3f}")
                cf3.metric("Changed features", counterfactual.get("changed_feature_count", 0))
                st.caption(
                    f"Goal label: {counterfactual['counterfactual_label']} | "
                    f"Feature source: {counterfactual['feature_source']} | "
                    f"Reference pool: {counterfactual['reference_label_source']}"
                )
                counterfactual_frame = _to_frame(counterfactual.get("changed_features", []))
                if counterfactual_frame.empty:
                    render_empty("No feature changes were required for the current counterfactual path.")
                else:
                    st.dataframe(
                        counterfactual_frame[["rank", "feature", "current_value", "counterfactual_value", "delta", "updated_anomaly_probability"]],
                        use_container_width=True,
                        hide_index=True,
                    )

        st.subheader("Feedback history")
        try:
            feedback_history = data.fetch_feedback_history(int(detail["id"]))
        except Exception as exc:
            render_error(f"Failed to load feedback history: {exc}")
            feedback_history = []

        feedback_frame = _to_frame(feedback_history)
        if feedback_frame.empty:
            render_empty("No analyst feedback has been recorded for this alert yet.")
        else:
            st.dataframe(
                feedback_frame[["created_at", "user_id", "label", "reason"]],
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("Incident history")
        try:
            incident_history = data.fetch_incident_history(int(detail["id"]))
        except Exception as exc:
            render_error(f"Failed to load incident history: {exc}")
            incident_history = []

        incident_frame = _to_frame(incident_history)
        if incident_frame.empty:
            render_empty("No incident transitions have been recorded for this alert yet.")
        else:
            st.dataframe(
                incident_frame[
                    [
                        "changed_at",
                        "changed_by",
                        "previous_status",
                        "new_status",
                        "previous_owner",
                        "new_owner",
                        "change_notes",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

    with playbook_col:
        st.subheader("Incident workflow")
        st.caption("Move alerts through NEW -> INVESTIGATING -> RESOLVED for operator handling.")
        user_id = current_user() or "dashboard"
        incident_options = ["NEW", "INVESTIGATING", "RESOLVED"]
        current_status = str(detail.get("incident_status") or "NEW")
        current_index = incident_options.index(current_status) if current_status in incident_options else 0
        incident_owner = st.text_input(
            "Incident owner",
            value=detail.get("incident_owner") or user_id,
            key=f"incident-owner-{detail['id']}",
        )
        next_status = st.selectbox(
            "Incident status",
            options=incident_options,
            index=current_index,
            key=f"incident-status-{detail['id']}",
        )
        incident_notes = st.text_area(
            "Incident notes",
            value=detail.get("incident_notes") or "",
            key=f"incident-notes-{detail['id']}",
            placeholder="What has been investigated, blocked, or resolved?",
        )
        if st.button("Update incident", key=f"incident-submit-{detail['id']}", use_container_width=True):
            try:
                data.submit_alert_incident(int(detail["id"]), next_status, incident_owner.strip(), incident_notes.strip(), user_id)
            except Exception as exc:
                render_error(f"Failed to update incident: {exc}")
            else:
                st.success("Incident updated.")
                st.rerun()

        st.caption(
            f"Last incident update: {format_timestamp(detail.get('incident_updated_at'))}"
            f" by {detail.get('incident_updated_by') or 'system'}"
        )

        st.subheader(playbook["title"])
        st.write(playbook["summary"])
        for step in playbook["steps"]:
            st.write(f"- {step}")

        st.subheader("Analyst feedback")
        st.caption("Store a reviewed label for this alert. This is the first step for future retraining workflows.")
        label = st.selectbox(
            "Reviewed label",
            options=["true_positive", "false_positive", "false_negative"],
            key=f"feedback-label-{detail['id']}",
        )
        reason = st.text_area(
            "Reason / notes",
            key=f"feedback-reason-{detail['id']}",
            placeholder="Why is this alert correct, noisy, or missing context?",
        )
        if st.button("Save feedback", key=f"feedback-submit-{detail['id']}", use_container_width=True):
            try:
                data.submit_alert_feedback(int(detail["id"]), label, reason.strip(), user_id)
            except Exception as exc:
                render_error(f"Failed to save feedback: {exc}")
            else:
                st.success("Feedback saved.")
                st.rerun()


def render_log_explorer_page() -> None:
    render_sidebar("Log Explorer", auto_refresh=False)

    with st.form("log_explorer_filters"):
        c1, c2, c3 = st.columns(3)
        hours = c1.selectbox("Time window", [1, 6, 24, 72], index=2)
        ip_query = c2.text_input("IP contains")
        endpoint_query = c3.text_input("Endpoint contains")
        c4, c5, c6 = st.columns(3)
        method = c4.selectbox("Method", ["ALL", "GET", "POST", "PUT", "DELETE"])
        status_text = c5.text_input("Status code")
        search_query = c6.text_input("Free text")
        submitted = st.form_submit_button("Load logs", use_container_width=True)

    if not submitted and "log_explorer_loaded" not in st.session_state:
        st.session_state["log_explorer_loaded"] = False
        render_empty("Choose filters and click 'Load logs' to query raw logs.")
        return

    st.session_state["log_explorer_loaded"] = True
    status_code = int(status_text) if status_text.strip().isdigit() else None

    try:
        logs = data.fetch_logs(int(hours), ip_query, endpoint_query, method, status_code, search_query)
    except Exception as exc:
        render_error(f"Failed to load logs: {exc}")
        return

    logs_frame = _to_frame(logs)
    if logs_frame.empty:
        render_empty("No logs matched the selected filters.")
        return

    st.subheader("Raw log search results")
    st.dataframe(
        logs_frame[["id", "timestamp", "ip", "method", "endpoint", "status", "response_time_ms", "user_agent"]],
        use_container_width=True,
        hide_index=True,
    )

    selected_log_id = st.selectbox(
        "Inspect log",
        options=logs_frame["id"].tolist(),
        format_func=lambda value: f"Log #{value} - {logs_frame.loc[logs_frame['id'] == value, 'endpoint'].iloc[0]}",
    )

    selected_log = logs_frame.loc[logs_frame["id"] == selected_log_id].iloc[0].to_dict()
    context = data.fetch_log_context(int(selected_log_id))

    left, right = st.columns((1.1, 1))
    with left:
        st.subheader(f"Log #{selected_log_id}")
        st.json(selected_log, expanded=False)
    with right:
        st.subheader("Related alerts")
        related_alerts = _to_frame(context.get("related_alerts", []))
        if related_alerts.empty:
            render_empty("No related alerts were found for this log.")
        else:
            st.dataframe(related_alerts, use_container_width=True, hide_index=True)
        st.subheader("Hybrid score")
        if context.get("hybrid_score"):
            st.json(context["hybrid_score"], expanded=False)
        else:
            render_empty("No hybrid score exists for this log.")


def render_model_monitoring_page() -> None:
    render_sidebar("Model Monitoring", auto_refresh=False)
    if st.button("Refresh now"):
        data.clear_dashboard_caches()
        st.rerun()

    try:
        overview = data.fetch_overview_snapshot()
        split_rows = data.fetch_prediction_split()
        alert_trend_rows = data.fetch_alert_trend(24)
        f1_history = data.fetch_ml_f1_history(24)
    except Exception as exc:
        render_error(f"Failed to load model monitoring data: {exc}")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Operational F1", f"{(overview.get('latest_f1') or 0):.3f}")
    c2.metric("Data freshness", humanize_seconds(overview.get("data_freshness_seconds")))
    c3.metric("Observed targets", len(overview.get("targets", [])))

    split_frame = _to_frame(split_rows)
    trend_frame = _to_frame(alert_trend_rows)

    left, right = st.columns(2)
    with left:
        st.subheader("Prediction split")
        if split_frame.empty:
            render_empty("No hybrid prediction data is available.")
        else:
            st.bar_chart(split_frame.set_index("prediction")["count"])
    with right:
        st.subheader("Alert trend (24h)")
        if trend_frame.empty:
            render_empty("No alert trend is available.")
        else:
            st.line_chart(trend_frame.set_index("bucket")["count"])

    st.subheader("Model performance over time")
    if f1_history.empty:
        render_empty("No Prometheus F1 history is available yet.")
    else:
        st.line_chart(f1_history.set_index("timestamp")["f1_score"])

