from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from src.dashboard import auth, data, pages_impl


class FakeStreamlit:
    def __init__(self, select_value=None, submitted=True, button_value=False):
        self.session_state = {}
        self.sidebar = self
        self._select_value = select_value
        self._submitted = submitted
        self._button_value = button_value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def columns(self, spec):
        count = spec if isinstance(spec, int) else len(spec)
        return [self for _ in range(count)]

    def form(self, *args, **kwargs):
        return self

    def set_page_config(self, *args, **kwargs):
        return None

    def markdown(self, *args, **kwargs):
        return None

    def title(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def text_input(self, *args, **kwargs):
        return ""

    def text_area(self, *args, **kwargs):
        return ""

    def form_submit_button(self, *args, **kwargs):
        return self._submitted

    def success(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def stop(self):
        raise RuntimeError("stopped")

    def rerun(self):
        return None

    def write(self, *args, **kwargs):
        return None

    def button(self, *args, **kwargs):
        return self._button_value

    def metric(self, *args, **kwargs):
        return None

    def subheader(self, *args, **kwargs):
        return None

    def dataframe(self, *args, **kwargs):
        return None

    def bar_chart(self, *args, **kwargs):
        return None

    def line_chart(self, *args, **kwargs):
        return None

    def area_chart(self, *args, **kwargs):
        return None

    def json(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def selectbox(self, label, options, **kwargs):
        if self._select_value in options:
            return self._select_value
        return options[0] if options else None


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_attempt_login_sets_session_state(monkeypatch):
    fake_st = FakeStreamlit()
    monkeypatch.setattr(auth, "st", fake_st)
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: SimpleNamespace(dashboard_username="admin", dashboard_password="secret"),
    )

    assert auth.attempt_login("admin", "secret") is True
    assert fake_st.session_state["dashboard_authenticated"] is True
    assert fake_st.session_state["dashboard_username"] == "admin"


def test_logout_clears_session_state(monkeypatch):
    fake_st = FakeStreamlit()
    fake_st.session_state.update(
        {"dashboard_authenticated": True, "dashboard_username": "analyst"}
    )
    monkeypatch.setattr(auth, "st", fake_st)

    auth.logout()

    assert fake_st.session_state == {}


def test_fetch_alerts_builds_expected_filters(monkeypatch):
    captured = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(data, "_rows", fake_rows)

    data._fetch_alerts("HIGH", "sql_injection", "rule", "INVESTIGATING", "10.0.0", 6, 50)

    assert "severity = %s" in captured["sql"]
    assert "alert_type = %s" in captured["sql"]
    assert "source = %s" in captured["sql"]
    assert "COALESCE(incident_status, 'NEW') = %s" in captured["sql"]
    assert "ip::text ILIKE %s" in captured["sql"]
    assert captured["params"] == [6, "HIGH", "sql_injection", "rule", "INVESTIGATING", "%10.0.0%", 50]


def test_fetch_feedback_history_uses_alert_id(monkeypatch):
    captured = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(data, "_rows", fake_rows)

    data._fetch_feedback_history(7, 5)

    assert "FROM feedback" in captured["sql"]
    assert captured["params"] == (7, 5)


def test_fetch_incident_history_uses_alert_id(monkeypatch):
    captured = {}

    def fake_rows(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(data, "_rows", fake_rows)

    data._fetch_incident_history(9, 4)

    assert "FROM alert_incident_history" in captured["sql"]
    assert captured["params"] == (9, 4)


def test_submit_alert_feedback_posts_and_clears_caches(monkeypatch):
    captured = {"cleared": False}

    def fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "created", "feedback": {"id": 1}}

    def fake_clear():
        captured["cleared"] = True

    monkeypatch.setattr(data, "_flask_post", fake_post)
    monkeypatch.setattr(data, "clear_dashboard_caches", fake_clear)

    result = data.submit_alert_feedback(3, "false_positive", "noise", "analyst")

    assert result["status"] == "created"
    assert captured["path"] == "/api/alerts/feedback"
    assert captured["payload"] == {
        "alert_id": 3,
        "label": "false_positive",
        "reason": "noise",
        "user_id": "analyst",
    }
    assert captured["cleared"] is True


def test_submit_alert_incident_posts_and_clears_caches(monkeypatch):
    captured = {"cleared": False}

    def fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return {"status": "updated", "incident": {"id": 3, "incident_status": "INVESTIGATING"}}

    def fake_clear():
        captured["cleared"] = True

    monkeypatch.setattr(data, "_flask_post", fake_post)
    monkeypatch.setattr(data, "clear_dashboard_caches", fake_clear)

    result = data.submit_alert_incident(3, "INVESTIGATING", "analyst", "triaged", "analyst")

    assert result["status"] == "updated"
    assert captured["path"] == "/api/alerts/incident"
    assert captured["payload"] == {
        "alert_id": 3,
        "incident_status": "INVESTIGATING",
        "incident_owner": "analyst",
        "incident_notes": "triaged",
        "user_id": "analyst",
    }
    assert captured["cleared"] is True


def test_prometheus_get_uses_configured_url(monkeypatch):
    called = {}

    def fake_get(url, params=None, timeout=None):
        called["url"] = url
        called["params"] = params
        called["timeout"] = timeout
        return FakeResponse({"status": "success", "data": {"result": []}})

    monkeypatch.setattr(data.requests, "get", fake_get)
    monkeypatch.setattr(
        data,
        "get_config",
        lambda: SimpleNamespace(prometheus_url="http://prometheus:9090"),
    )

    payload = data._prometheus_get("/api/v1/query", {"query": "up"})

    assert payload["status"] == "success"
    assert called["url"] == "http://prometheus:9090/api/v1/query"
    assert called["params"] == {"query": "up"}


def test_parse_vector_value_returns_float():
    payload = {"data": {"result": [{"value": [1710000000, "0.783"]}]}}
    assert data._parse_vector_value(payload) == 0.783


def test_range_to_frame_creates_dataframe():
    payload = {
        "data": {
            "result": [
                {
                    "metric": {"model": "hybrid_ensemble"},
                    "values": [[1710000000, "0.7"], [1710000300, "0.8"]],
                }
            ]
        }
    }

    frame = data._range_to_frame(payload, "f1_score")

    assert list(frame.columns) == ["timestamp", "f1_score", "series"]
    assert frame["f1_score"].tolist() == [0.7, 0.8]


def test_render_overview_page_smoke(monkeypatch):
    fake_st = FakeStreamlit()
    monkeypatch.setattr(pages_impl, "st", fake_st)
    monkeypatch.setattr(pages_impl, "st_autorefresh", lambda **kwargs: None)
    monkeypatch.setattr(pages_impl, "render_sidebar", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_overview_snapshot",
        lambda: {
            "total_alerts": 10,
            "active_alerts": [{"severity": "HIGH", "count": 3}],
            "latest_f1": 0.81,
            "data_freshness_seconds": 42,
            "targets": [{"job": "flask_app", "health": "up", "instance": "flask-app:5001"}],
            "recent_alerts": [{"bucket": pd.Timestamp("2026-04-13T12:00:00Z"), "count": 4}],
            "recent_anomalies": [{"bucket": pd.Timestamp("2026-04-13T12:00:00Z"), "count": 2}],
            "latest_log_timestamp": pd.Timestamp("2026-04-13T12:05:00Z"),
        },
    )

    pages_impl.render_overview_page()


def test_render_alerts_page_smoke(monkeypatch):
    fake_st = FakeStreamlit(select_value=1)
    monkeypatch.setattr(pages_impl, "st", fake_st)
    monkeypatch.setattr(pages_impl, "st_autorefresh", lambda **kwargs: None)
    monkeypatch.setattr(pages_impl, "render_sidebar", lambda *args, **kwargs: None)
    monkeypatch.setattr(pages_impl, "current_user", lambda: "analyst")
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_alert_options",
        lambda: {"severities": ["HIGH"], "types": ["sql_injection"], "sources": ["rule"], "statuses": ["NEW", "INVESTIGATING", "RESOLVED"]},
    )
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_alerts",
        lambda *args, **kwargs: [
            {
                "id": 1,
                "alert_type": "sql_injection",
                "severity": "HIGH",
                "source": "rule",
                "ip": "10.0.0.5",
                "timestamp": pd.Timestamp("2026-04-13T12:00:00Z"),
                "incident_status": "NEW",
                "incident_owner": "analyst",
                "related_logs": 2,
                "confidence": 1.0,
            }
        ],
    )
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_alert_detail",
        lambda alert_id: {
            "id": alert_id,
            "alert_type": "sql_injection",
            "severity": "HIGH",
            "source": "rule",
            "confidence": 1.0,
            "ip": "10.0.0.5",
            "timestamp": pd.Timestamp("2026-04-13T12:00:00Z"),
            "description": "Test alert",
            "metadata": {"attempts": 2},
            "log_ids": [1001, 1002],
            "incident_status": "NEW",
            "incident_owner": "analyst",
            "incident_notes": "queued",
            "incident_updated_at": pd.Timestamp("2026-04-13T12:05:00Z"),
            "incident_updated_by": "analyst",
        },
    )
    monkeypatch.setattr(pages_impl.data, "fetch_logs_for_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(pages_impl.data, "fetch_feedback_history", lambda *args, **kwargs: [])
    monkeypatch.setattr(pages_impl.data, "fetch_incident_history", lambda *args, **kwargs: [])

    pages_impl.render_alerts_page()


def test_render_log_explorer_page_smoke(monkeypatch):
    fake_st = FakeStreamlit(select_value=1001, submitted=True)
    monkeypatch.setattr(pages_impl, "st", fake_st)
    monkeypatch.setattr(pages_impl, "render_sidebar", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_logs",
        lambda *args, **kwargs: [
            {
                "id": 1001,
                "timestamp": pd.Timestamp("2026-04-13T12:00:00Z"),
                "ip": "10.0.0.5",
                "method": "GET",
                "endpoint": "/search?q=test",
                "status": 200,
                "response_time_ms": 23.0,
                "user_agent": "pytest",
                "data": {"query": "test"},
            }
        ],
    )
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_log_context",
        lambda log_id: {"hybrid_score": {"log_id": log_id, "final_score": 0.91}, "related_alerts": []},
    )

    pages_impl.render_log_explorer_page()


def test_render_model_monitoring_page_smoke(monkeypatch):
    fake_st = FakeStreamlit()
    monkeypatch.setattr(pages_impl, "st", fake_st)
    monkeypatch.setattr(pages_impl, "render_sidebar", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_overview_snapshot",
        lambda: {"latest_f1": 0.84, "data_freshness_seconds": 90, "targets": [{"job": "prometheus"}]},
    )
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_prediction_split",
        lambda: [{"prediction": "anomaly", "count": 5}, {"prediction": "normal", "count": 20}],
    )
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_alert_trend",
        lambda hours=24: [{"bucket": pd.Timestamp("2026-04-13T12:00:00Z"), "count": 3}],
    )
    monkeypatch.setattr(
        pages_impl.data,
        "fetch_ml_f1_history",
        lambda hours=24: pd.DataFrame(
            [{"timestamp": pd.Timestamp("2026-04-13T12:00:00Z"), "f1_score": 0.84, "series": "hybrid_ensemble"}]
        ),
    )

    pages_impl.render_model_monitoring_page()


