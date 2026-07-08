from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.db.schema_checks import MissingSchemaError, assert_incident_workflow_schema, assert_rule_engine_schema


def _connection_for_schema(columns_by_table: dict[str, set[str]], existing_objects: set[str]):
    conn = MagicMock()

    def cursor_factory():
        cursor = MagicMock()

        def execute(sql, params):
            cursor._last_sql = sql
            cursor._last_params = params

        def fetchall():
            table_name = cursor._last_params[0]
            return [(column_name,) for column_name in sorted(columns_by_table.get(table_name, set()))]

        def fetchone():
            object_name = cursor._last_params[0]
            return (object_name,) if object_name in existing_objects else (None,)

        cursor.execute.side_effect = execute
        cursor.fetchall.side_effect = fetchall
        cursor.fetchone.side_effect = fetchone
        return cursor

    conn.cursor.side_effect = cursor_factory
    return conn


def test_assert_rule_engine_schema_accepts_migrated_schema():
    conn = _connection_for_schema({"alerts": {"dedup_key"}}, {"public.idx_alerts_rule_dedup"})

    assert_rule_engine_schema(conn)


def test_assert_rule_engine_schema_raises_for_missing_index():
    conn = _connection_for_schema({"alerts": {"dedup_key"}}, set())

    with pytest.raises(MissingSchemaError, match="idx_alerts_rule_dedup"):
        assert_rule_engine_schema(conn)


def test_assert_incident_workflow_schema_accepts_migrated_schema():
    conn = _connection_for_schema(
        {
            "alerts": {
                "incident_status",
                "incident_owner",
                "incident_notes",
                "incident_updated_at",
                "incident_updated_by",
            }
        },
        {
            "public.alert_incident_history",
            "public.idx_alert_incident_history_alert_id",
            "public.idx_alert_incident_history_changed_at",
        },
    )

    assert_incident_workflow_schema(conn)


def test_assert_incident_workflow_schema_raises_for_missing_columns_and_table():
    conn = _connection_for_schema({"alerts": {"incident_status"}}, set())

    with pytest.raises(MissingSchemaError, match="alert_incident_history"):
        assert_incident_workflow_schema(conn)
