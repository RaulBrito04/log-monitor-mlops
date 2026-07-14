from __future__ import annotations

from src.db.config import build_sqlalchemy_url, build_sqlalchemy_url_from_env


class TestDbConfig:
    def test_build_sqlalchemy_url_quotes_credentials(self):
        url = build_sqlalchemy_url(
            host="db.internal",
            port=5432,
            database="log monitor",
            user="user@example.com",
            password="p@ss word",
        )

        assert url == (
            "postgresql+psycopg2://user%40example.com:p%40ss+word"
            "@db.internal:5432/log+monitor"
        )

    def test_build_sqlalchemy_url_from_env_uses_explicit_override(self, monkeypatch):
        monkeypatch.setenv("ALEMBIC_DATABASE_URL", "postgresql+psycopg2://override")

        assert build_sqlalchemy_url_from_env() == "postgresql+psycopg2://override"

    def test_build_sqlalchemy_url_from_env_uses_postgres_env_vars(self, monkeypatch):
        monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "postgres")
        monkeypatch.setenv("POSTGRES_PORT", "5433")
        monkeypatch.setenv("POSTGRES_DB", "logmonitor")
        monkeypatch.setenv("POSTGRES_USER", "postgres")
        monkeypatch.setenv("POSTGRES_PASSWORD", "secret")

        assert build_sqlalchemy_url_from_env() == "postgresql+psycopg2://postgres:secret@postgres:5433/logmonitor"
