from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DashboardConfig:
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str
    prometheus_url: str
    flask_api_url: str
    dashboard_username: str
    dashboard_password: str
    refresh_seconds: int


def get_config() -> DashboardConfig:
    return DashboardConfig(
        postgres_host=os.getenv("POSTGRES_HOST", "localhost"),
        postgres_port=int(os.getenv("POSTGRES_PORT", "5432")),
        postgres_db=os.getenv("POSTGRES_DB", "logmonitor"),
        postgres_user=os.getenv("POSTGRES_USER", "postgres"),
        postgres_password=os.getenv("POSTGRES_PASSWORD", "changeme_em_prod"),
        prometheus_url=os.getenv("PROMETHEUS_URL", "http://localhost:9090").rstrip("/"),
        flask_api_url=os.getenv("FLASK_API_URL", "http://localhost:5001").rstrip("/"),
        dashboard_username=os.getenv("DASHBOARD_USERNAME", "admin"),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", "admin"),
        refresh_seconds=max(5, int(os.getenv("DASHBOARD_REFRESH_SECONDS", "30"))),
    )