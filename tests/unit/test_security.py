from __future__ import annotations

from importlib import reload
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest


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
        "RATELIMIT_LOGIN": "5 per minute",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch("src.flask_app.app.PrometheusMetrics") as metrics_cls, patch(
            "src.flask_app.app.validate_incident_workflow_schema"
        ):
            metrics_cls.return_value.info = MagicMock()
            from src.flask_app.app import create_app

            app = create_app()
    app.config.update({"TESTING": True, "LOG_FILE": "/tmp/test_app.log"})
    yield app


@pytest.fixture
def client(app):
    with app.test_client() as client:
        yield client


class TestSecurityHeaders:
    def test_x_frame_options_present(self, client):
        response = client.get("/health")
        assert response.headers.get("X-Frame-Options") == "DENY"

    def test_x_content_type_options_present(self, client):
        response = client.get("/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_csp_header_present(self, client):
        response = client.get("/health")
        assert "Content-Security-Policy" in response.headers

    def test_referrer_policy_present(self, client):
        response = client.get("/health")
        assert response.headers.get("Referrer-Policy") == "no-referrer"

    def test_auth_response_has_no_cache(self, client):
        response = client.post("/login", json={"username": "x", "password": "y"})
        assert "no-store" in response.headers.get("Cache-Control", "")


class TestRateLimiting:
    def test_login_rate_limit_triggered(self, client):
        for _ in range(5):
            client.post(
                "/login",
                json={"username": "x", "password": "wrong"},
                environ_overrides={"REMOTE_ADDR": "10.0.0.10"},
            )
        response = client.post(
            "/login",
            json={"username": "x", "password": "wrong"},
            environ_overrides={"REMOTE_ADDR": "10.0.0.10"},
        )
        assert response.status_code == 429

    def test_rate_limit_response_is_json(self, client):
        response = None
        for _ in range(6):
            response = client.post(
                "/login",
                json={"username": "x", "password": "wrong"},
                environ_overrides={"REMOTE_ADDR": "10.0.0.11"},
            )
        assert response is not None
        assert response.is_json
        data = response.get_json()
        assert data["error"] == "too_many_requests"


class TestInputValidation:
    def test_login_empty_body_returns_400(self, client):
        response = client.post("/login", data="not json", content_type="text/plain")
        assert response.status_code == 400

    def test_login_missing_password_returns_400(self, client):
        response = client.post("/login", json={"username": "admin"})
        assert response.status_code == 400

    def test_login_username_too_long_returns_400(self, client):
        response = client.post("/login", json={"username": "a" * 65, "password": "pass"})
        assert response.status_code == 400

    def test_login_username_with_special_chars_rejected(self, client):
        response = client.post(
            "/login",
            json={"username": "admin'; DROP TABLE users;--", "password": "pass"},
        )
        assert response.status_code == 400

    def test_upload_wrong_extension_rejected(self, client):
        data = {"file": (BytesIO(b"malicious content"), "malware.exe")}
        response = client.post("/api/upload", data=data, content_type="multipart/form-data")
        assert response.status_code == 400

    def test_413_response_for_large_payload(self, client, app):
        app.config["MAX_CONTENT_LENGTH"] = 4
        response = client.post("/api/upload", data=b"0123456789")
        assert response.status_code == 413


class TestSecretsConfig:
    def test_short_secret_key_raises(self):
        with patch.dict("os.environ", {"LOGMONITOR_ENV": "production", "FLASK_SECRET_KEY": "short"}, clear=False):
            import src.flask_app.config as cfg

            with pytest.raises(ValueError, match="demasiado curta"):
                reload(cfg)

    def test_missing_key_in_production_raises(self):
        with patch.dict("os.environ", {"LOGMONITOR_ENV": "production", "FLASK_SECRET_KEY": ""}, clear=False):
            import src.flask_app.config as cfg

            with pytest.raises(EnvironmentError, match="obrigatoria"):
                reload(cfg)

    def test_demo_users_empty_in_production(self):
        with patch.dict("os.environ", {"LOGMONITOR_ENV": "production", "FLASK_SECRET_KEY": "a" * 32}, clear=False):
            import src.flask_app.config as cfg

            reload(cfg)
            assert cfg.get_demo_users() == {}


class TestMLTrustBoundary:
    def test_path_outside_models_dir_rejected(self, tmp_path):
        from src.ml.hybrid_pipeline import _safe_load_pickle

        evil_path = tmp_path / "evil.pkl"
        evil_path.write_bytes(b"x")
        with pytest.raises(ValueError, match="fora do diretorio"):
            _safe_load_pickle(str(evil_path), allowed_dir="models")

    def test_wrong_extension_rejected(self, tmp_path):
        from src.ml.hybrid_pipeline import _safe_load_pickle

        models_dir = tmp_path / "models"
        models_dir.mkdir(parents=True)
        bad_ext = models_dir / "model.json"
        bad_ext.write_bytes(b"x")
        with pytest.raises(ValueError, match="Extensao invalida"):
            _safe_load_pickle(str(bad_ext), allowed_dir=str(models_dir))
