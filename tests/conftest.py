from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import psycopg2
import pytest
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

from src.db.config import build_sqlalchemy_url

load_dotenv()
load_dotenv("docker/.env")

TEST_DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "database": "logmonitor_test",
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "changeme_em_prod"),
}

ADMIN_DB_NAME = os.getenv("POSTGRES_DB", "logmonitor")
ALEMBIC_INI_PATH = Path("alembic.ini")


def _terminate_db_connections(cursor, database_name: str) -> None:
    cursor.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = %s AND pid <> pg_backend_pid()
        """,
        (database_name,),
    )


def _apply_schema_via_alembic(db_config: dict[str, object]) -> None:
    alembic_cfg = Config(str(ALEMBIC_INI_PATH))
    alembic_cfg.set_main_option(
        "sqlalchemy.url",
        build_sqlalchemy_url(
            host=str(db_config["host"]),
            port=str(db_config["port"]),
            database=str(db_config["database"]),
            user=str(db_config["user"]),
            password=str(db_config["password"]),
        ),
    )
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
def test_db() -> dict[str, object]:
    try:
        admin_conn = psycopg2.connect(
            host=TEST_DB_CONFIG["host"],
            port=TEST_DB_CONFIG["port"],
            database=ADMIN_DB_NAME,
            user=TEST_DB_CONFIG["user"],
            password=TEST_DB_CONFIG["password"],
        )
    except psycopg2.OperationalError as exc:
        pytest.skip(f"PostgreSQL test DB unavailable: {exc}")

    admin_conn.autocommit = True
    admin_cursor = admin_conn.cursor()
    _terminate_db_connections(admin_cursor, TEST_DB_CONFIG["database"])
    admin_cursor.execute(f"DROP DATABASE IF EXISTS {TEST_DB_CONFIG['database']}")
    admin_cursor.execute(f"CREATE DATABASE {TEST_DB_CONFIG['database']}")
    admin_cursor.close()
    admin_conn.close()

    _apply_schema_via_alembic(TEST_DB_CONFIG)

    yield TEST_DB_CONFIG

    admin_conn = psycopg2.connect(
        host=TEST_DB_CONFIG["host"],
        port=TEST_DB_CONFIG["port"],
        database=ADMIN_DB_NAME,
        user=TEST_DB_CONFIG["user"],
        password=TEST_DB_CONFIG["password"],
    )
    admin_conn.autocommit = True
    admin_cursor = admin_conn.cursor()
    _terminate_db_connections(admin_cursor, TEST_DB_CONFIG["database"])
    admin_cursor.execute(f"DROP DATABASE IF EXISTS {TEST_DB_CONFIG['database']}")
    admin_cursor.close()
    admin_conn.close()


@pytest.fixture
def db_conn(test_db: dict[str, object]):
    conn = psycopg2.connect(**test_db)
    conn.autocommit = False
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def clean_db():
    yield


@pytest.fixture
def sample_logs() -> list[dict[str, object]]:
    logs = []
    base_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    endpoints = ["/api/users", "/api/data", "/login", "/health", "/search"]
    methods = ["GET", "POST", "PUT", "DELETE"]
    statuses = [200, 200, 200, 201, 400, 404, 500]

    for i in range(100):
        logs.append(
            {
                "log_type": "web",
                "timestamp": (base_time + timedelta(seconds=i * 10)).isoformat(),
                "ip": f"192.168.1.{random.randint(1, 20)}",
                "method": random.choice(methods),
                "endpoint": random.choice(endpoints),
                "status": random.choice(statuses),
                "response_time_ms": random.randint(10, 500),
                "user_agent": "Mozilla/5.0",
            }
        )
    return logs


@pytest.fixture
def anomalous_logs() -> list[dict[str, object]]:
    base_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    brute_force = [
        {
            "log_type": "web",
            "timestamp": (base_time + timedelta(seconds=i * 5)).isoformat(),
            "ip": "10.0.0.99",
            "method": "POST",
            "endpoint": "/login",
            "status": 401,
            "response_time_ms": 50,
            "user_agent": "python-requests/2.31.0",
        }
        for i in range(6)
    ]
    sql_injection = [
        {
            "log_type": "web",
            "timestamp": base_time.isoformat(),
            "ip": "10.0.0.88",
            "method": "GET",
            "endpoint": "/api/users?id=1 OR 1=1 --",
            "status": 400,
            "response_time_ms": 120,
            "user_agent": "sqlmap/1.0",
        }
    ]
    return brute_force + sql_injection


@pytest.fixture
def mock_model() -> MagicMock:
    model = MagicMock()
    model.decision_function.return_value = [-0.3]
    return model


@pytest.fixture
def mock_scaler() -> MagicMock:
    scaler = MagicMock()
    scaler.transform.side_effect = lambda X: X
    return scaler
