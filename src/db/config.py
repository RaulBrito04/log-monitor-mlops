from __future__ import annotations

import os
from urllib.parse import quote_plus


def build_sqlalchemy_url(*, host: str, port: int | str, database: str, user: str, password: str) -> str:
    return (
        f"postgresql+psycopg2://{quote_plus(str(user))}:{quote_plus(str(password))}"
        f"@{host}:{port}/{quote_plus(str(database))}"
    )


def build_sqlalchemy_url_from_env(default_database: str = "logmonitor") -> str:
    explicit_url = os.getenv("ALEMBIC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if explicit_url:
        return explicit_url

    return build_sqlalchemy_url(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=os.getenv("POSTGRES_DB", default_database),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme"),
    )
