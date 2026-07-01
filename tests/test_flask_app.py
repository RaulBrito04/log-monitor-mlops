"""
Tests for the Flask application.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.flask_app.app import create_app


@pytest.fixture
def app():
    env = {
        "LOGMONITOR_ENV": "test",
        "FLASK_SECRET_KEY": "a" * 32,
        "DEMO_ADMIN_USER": "admin",
        "DEMO_ADMIN_PASS": "admin123",
        "DEMO_USER1_USER": "user1",
        "DEMO_USER1_PASS": "password1",
        "DEMO_USER2_USER": "user2",
        "DEMO_USER2_PASS": "password2",
        "DEMO_ANALYST_USER": "analyst",
        "DEMO_ANALYST_PASS": "analyst99",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch("src.flask_app.app.PrometheusMetrics") as metrics_cls:
            metrics_cls.return_value.info = MagicMock()
            app = create_app()
    app.config.update({"TESTING": True, "LOG_FILE": "/tmp/test_app.log"})
    yield app


@pytest.fixture
def client(app):
    with app.test_client() as client:
        yield client


@pytest.fixture
def admin_token(client):
    response = client.post("/login", json={"username": "admin", "password": "admin123"})
    return response.get_json()["token"]


class TestHealthCheck:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_json(self, client):
        response = client.get("/health")
        data = response.get_json()
        assert data["status"] == "healthy"
        assert "version" in data

    def test_health_has_request_id_header(self, client):
        response = client.get("/health")
        assert "X-Request-ID" in response.headers


class TestLogin:
    def test_valid_admin_login_returns_200(self, client):
        response = client.post("/login", json={"username": "admin", "password": "admin123"})
        assert response.status_code == 200

    def test_valid_login_returns_token(self, client):
        response = client.post("/login", json={"username": "user1", "password": "password1"})
        data = response.get_json()
        assert "token" in data
        assert "role" in data

    def test_invalid_password_returns_401(self, client):
        response = client.post("/login", json={"username": "admin", "password": "wrongpassword"})
        assert response.status_code == 401

    def test_nonexistent_user_returns_401(self, client):
        response = client.post("/login", json={"username": "nonexistent", "password": "anything"})
        assert response.status_code == 401

    def test_missing_password_returns_400(self, client):
        response = client.post("/login", json={"username": "admin"})
        assert response.status_code == 400

    def test_missing_username_returns_400(self, client):
        response = client.post("/login", json={"password": "admin123"})
        assert response.status_code == 400

    def test_empty_body_returns_400(self, client):
        response = client.post("/login", data="not json", content_type="text/plain")
        assert response.status_code == 400

    def test_all_valid_users_can_login(self, client):
        credentials = {
            "admin": "admin123",
            "user1": "password1",
            "user2": "password2",
            "analyst": "analyst99",
        }
        for username, password in credentials.items():
            response = client.post("/login", json={"username": username, "password": password})
            assert response.status_code == 200, f"Login failed for {username}"


class TestApiData:
    def test_returns_200(self, client):
        response = client.get("/api/data")
        assert response.status_code == 200

    def test_returns_data_list(self, client):
        response = client.get("/api/data")
        data = response.get_json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_default_pagination(self, client):
        response = client.get("/api/data")
        data = response.get_json()
        assert data["page"] == 1
        assert data["per_page"] == 10
        assert len(data["data"]) == 10

    def test_custom_pagination(self, client):
        response = client.get("/api/data?page=2&per_page=5")
        data = response.get_json()
        assert data["page"] == 2
        assert data["per_page"] == 5
        assert len(data["data"]) == 5

    def test_invalid_page_returns_400(self, client):
        response = client.get("/api/data?page=abc")
        assert response.status_code == 400

    def test_per_page_capped_at_20(self, client):
        response = client.get("/api/data?per_page=100")
        assert response.status_code == 400

    def test_returns_total_count(self, client):
        response = client.get("/api/data")
        data = response.get_json()
        assert "total" in data
        assert data["total"] > 0


class TestApiUsers:
    def test_returns_200(self, client):
        response = client.get("/api/users")
        assert response.status_code == 200

    def test_returns_users_list(self, client):
        response = client.get("/api/users")
        data = response.get_json()
        assert "users" in data
        assert isinstance(data["users"], list)
        assert len(data["users"]) > 0

    def test_users_have_required_fields(self, client):
        response = client.get("/api/users")
        users = response.get_json()["users"]
        for user in users:
            assert "id" in user
            assert "username" in user
            assert "email" in user


class TestAdminPanel:
    def test_without_token_returns_401(self, client):
        response = client.get("/admin")
        assert response.status_code == 401

    def test_with_invalid_token_returns_403(self, client):
        response = client.get("/admin", headers={"Authorization": "Bearer invalid_token"})
        assert response.status_code == 403

    def test_with_admin_token_returns_200(self, client, admin_token):
        response = client.get("/admin", headers={"Authorization": f"Bearer {admin_token}"})
        assert response.status_code == 200

    def test_admin_response_has_system_info(self, client, admin_token):
        response = client.get("/admin", headers={"Authorization": f"Bearer {admin_token}"})
        data = response.get_json()
        assert "system_info" in data

    def test_non_admin_user_returns_403(self, client):
        login_response = client.post("/login", json={"username": "user1", "password": "password1"})
        token = login_response.get_json()["token"]
        response = client.get("/admin", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403


class TestSearch:
    def test_valid_query_returns_200(self, client):
        response = client.get("/search?q=data")
        assert response.status_code == 200

    def test_returns_results_and_count(self, client):
        response = client.get("/search?q=data")
        data = response.get_json()
        assert "results" in data
        assert "count" in data
        assert "query" in data

    def test_missing_query_returns_400(self, client):
        response = client.get("/search")
        assert response.status_code == 400

    def test_sql_injection_payload_does_not_crash(self, client):
        sqli_payloads = ["' OR 1=1 --", "'; DROP TABLE users; --", "' UNION SELECT * FROM users --"]
        for payload in sqli_payloads:
            response = client.get(f"/search?q={payload}")
            assert response.status_code in (200, 400), f"App crashed with payload: {payload!r}"


class TestUpload:
    def test_upload_returns_200_without_file(self, client):
        response = client.post("/api/upload", data=b"sample")
        assert response.status_code == 200


class TestErrorHandlers:
    def test_unknown_endpoint_returns_404(self, client):
        response = client.get("/this/endpoint/does/not/exist")
        assert response.status_code == 404

    def test_404_response_is_json(self, client):
        response = client.get("/nonexistent")
        data = response.get_json()
        assert "error" in data

    def test_wrong_method_returns_405(self, client):
        response = client.delete("/health")
        assert response.status_code == 405


class TestMiddleware:
    def test_all_responses_have_request_id(self, client):
        endpoints = ["/health", "/api/data", "/api/users", "/nonexistent"]
        for endpoint in endpoints:
            response = client.get(endpoint)
            assert "X-Request-ID" in response.headers, f"Missing X-Request-ID for {endpoint}"

    def test_request_ids_are_unique(self, client):
        ids = set()
        for _ in range(10):
            response = client.get("/health")
            request_id = response.headers.get("X-Request-ID")
            assert request_id not in ids, "Duplicate request ID detected"
            ids.add(request_id)

class TestAlertFeedback:
    def test_valid_feedback_returns_201(self, client):
        created_at = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
        with patch("src.flask_app.app._persist_feedback", return_value=(11, created_at)) as persist_feedback:
            response = client.post(
                "/api/alerts/feedback",
                json={
                    "alert_id": 7,
                    "label": "false_positive",
                    "reason": "Known scanner noise",
                    "user_id": "analyst",
                },
            )

        assert response.status_code == 201
        payload = response.get_json()
        assert payload["status"] == "created"
        assert payload["feedback"]["id"] == 11
        assert payload["feedback"]["alert_id"] == 7
        assert payload["feedback"]["label"] == "false_positive"
        persist_feedback.assert_called_once_with(
            alert_id=7,
            user_id="analyst",
            label="false_positive",
            reason="Known scanner noise",
        )

    def test_invalid_feedback_label_returns_400(self, client):
        response = client.post(
            "/api/alerts/feedback",
            json={
                "alert_id": 7,
                "label": "maybe",
                "reason": "invalid",
                "user_id": "analyst",
            },
        )

        assert response.status_code == 400
        payload = response.get_json()
        assert payload["error"] == "invalid_request"

    def test_feedback_requires_json_body(self, client):
        response = client.post("/api/alerts/feedback", data="plain text", content_type="text/plain")

        assert response.status_code == 400
        payload = response.get_json()
        assert payload["error"] == "Request body must be JSON"

    def test_unknown_alert_returns_404(self, client):
        with patch("src.flask_app.app._persist_feedback", side_effect=LookupError("missing alert")):
            response = client.post(
                "/api/alerts/feedback",
                json={
                    "alert_id": 999,
                    "label": "true_positive",
                    "reason": "reviewed",
                    "user_id": "analyst",
                },
            )

        assert response.status_code == 404
        payload = response.get_json()
        assert payload["error"] == "not_found"
class TestAlertIncidentWorkflow:
    def test_valid_incident_update_returns_200(self, client):
        with patch(
            "src.flask_app.app._update_alert_incident",
            return_value={
                "id": 7,
                "incident_status": "INVESTIGATING",
                "incident_owner": "analyst",
                "incident_notes": "triaged",
                "incident_updated_at": "2026-07-01T11:20:00+00:00",
                "incident_updated_by": "analyst",
                "history_id": 21,
                "history_changed_at": "2026-07-01T11:20:00+00:00",
            },
        ) as update_incident:
            response = client.post(
                "/api/alerts/incident",
                json={
                    "alert_id": 7,
                    "incident_status": "INVESTIGATING",
                    "incident_owner": "analyst",
                    "incident_notes": "triaged",
                    "user_id": "analyst",
                },
            )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["status"] == "updated"
        assert payload["incident"]["incident_status"] == "INVESTIGATING"
        assert payload["incident"]["history_id"] == 21
        update_incident.assert_called_once_with(
            alert_id=7,
            incident_status="INVESTIGATING",
            incident_owner="analyst",
            incident_notes="triaged",
            user_id="analyst",
        )

    def test_invalid_incident_transition_returns_400(self, client):
        with patch("src.flask_app.app._update_alert_incident", side_effect=ValueError("invalid transition: RESOLVED -> INVESTIGATING")):
            response = client.post(
                "/api/alerts/incident",
                json={
                    "alert_id": 7,
                    "incident_status": "INVESTIGATING",
                    "incident_owner": "analyst",
                    "incident_notes": "reopen",
                    "user_id": "analyst",
                },
            )

        assert response.status_code == 400
        payload = response.get_json()
        assert payload["error"] == "invalid_transition"

    def test_incident_update_unknown_alert_returns_404(self, client):
        with patch("src.flask_app.app._update_alert_incident", side_effect=LookupError("missing alert")):
            response = client.post(
                "/api/alerts/incident",
                json={
                    "alert_id": 999,
                    "incident_status": "INVESTIGATING",
                    "incident_owner": "analyst",
                    "incident_notes": "triaged",
                    "user_id": "analyst",
                },
            )

        assert response.status_code == 404
        payload = response.get_json()
        assert payload["error"] == "not_found"

