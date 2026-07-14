from __future__ import annotations

from collections.abc import Iterable


MIGRATION_HINT = (
    "Apply database migrations with `alembic upgrade head` or run the `db-migrate` "
    "bootstrap service before starting runtime services."
)


class MissingSchemaError(RuntimeError):
    """Raised when a runtime service starts without the required migrated schema."""


def _fetch_column_names(conn, table_name: str) -> set[str]:
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            """,
            (table_name,),
        )
        return {str(row[0]) for row in cursor.fetchall()}
    finally:
        cursor.close()


def _object_exists(conn, regclass_name: str) -> bool:
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT to_regclass(%s)", (regclass_name,))
        row = cursor.fetchone()
        return bool(row and row[0] is not None)
    finally:
        cursor.close()


def _format_missing(kind: str, names: Iterable[str]) -> list[str]:
    return [f"{kind} {name}" for name in names]


def _raise_missing_schema(service_name: str, missing_parts: list[str]) -> None:
    details = ", ".join(missing_parts)
    raise MissingSchemaError(f"{service_name} requires migrated schema objects: {details}. {MIGRATION_HINT}")


def assert_rule_engine_schema(conn) -> None:
    alert_columns = _fetch_column_names(conn, "alerts")
    missing_parts: list[str] = []

    missing_columns = sorted({"dedup_key"} - alert_columns)
    if missing_columns:
        missing_parts.extend(_format_missing("column", [f"alerts.{name}" for name in missing_columns]))

    if not _object_exists(conn, "public.idx_alerts_rule_dedup"):
        missing_parts.append("index idx_alerts_rule_dedup")

    if missing_parts:
        _raise_missing_schema("rule_engine", missing_parts)


def assert_incident_workflow_schema(conn) -> None:
    alert_columns = _fetch_column_names(conn, "alerts")
    missing_parts: list[str] = []

    required_columns = {
        "incident_status",
        "incident_owner",
        "incident_notes",
        "incident_updated_at",
        "incident_updated_by",
    }
    missing_columns = sorted(required_columns - alert_columns)
    if missing_columns:
        missing_parts.extend(_format_missing("column", [f"alerts.{name}" for name in missing_columns]))

    if not _object_exists(conn, "public.alert_incident_history"):
        missing_parts.append("table alert_incident_history")
    if not _object_exists(conn, "public.idx_alert_incident_history_alert_id"):
        missing_parts.append("index idx_alert_incident_history_alert_id")
    if not _object_exists(conn, "public.idx_alert_incident_history_changed_at"):
        missing_parts.append("index idx_alert_incident_history_changed_at")

    if missing_parts:
        _raise_missing_schema("flask_app incident workflow", missing_parts)
