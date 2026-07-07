from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.detection import rule_engine


class TestRuleDefinitions:
    def test_get_rules_returns_six_queries(self):
        rules = rule_engine.get_rules("7 days")
        assert len(rules) == 6
        assert all("7 days" in sql for _, sql in rules)
        assert all("dedup_key" in sql for _, sql in rules)
        assert all("ON CONFLICT (source, dedup_key)" in sql for _, sql in rules)

    def test_sql_injection_rule_contains_expected_patterns(self):
        rules = dict(rule_engine.get_rules("60 seconds"))
        sql = rules["SQL Injection Detection"]
        assert "union" in sql.lower()
        assert "or%1=1" in sql.lower()
        assert "drop%table" in sql.lower()


class TestSchemaPreparation:
    def test_ensure_alert_dedup_schema_applies_migration_statements(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor

        rule_engine.ensure_alert_dedup_schema(conn)

        assert cursor.execute.call_count == 2
        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()
        cursor.close.assert_called_once()

    def test_ensure_alert_dedup_schema_rolls_back_on_error(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = [None, RuntimeError("boom")]
        conn.cursor.return_value = cursor

        with pytest.raises(RuntimeError, match="boom"):
            rule_engine.ensure_alert_dedup_schema(conn)

        conn.rollback.assert_called_once()
        cursor.close.assert_called_once()


class TestRuleExecution:
    def test_execute_rule_returns_summary_on_success(self):
        cursor = MagicMock()
        cursor.rowcount = 3

        result = rule_engine.execute_rule(cursor, "Brute Force Detection", "SELECT 1")

        cursor.execute.assert_called_once_with("SELECT 1")
        assert result["rule_name"] == "Brute Force Detection"
        assert result["stage"] == "brute_force_detection"
        assert result["alerts_created"] == 3
        assert result["error"] is None

    def test_execute_rule_returns_zero_on_exception(self):
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("boom")

        result = rule_engine.execute_rule(cursor, "Brute Force Detection", "SELECT 1")

        assert result["alerts_created"] == 0
        assert result["error"] == "boom"

    def test_run_once_accumulates_alert_counts(self):
        cursor = MagicMock()
        with patch.object(
            rule_engine,
            "execute_rule",
            side_effect=[
                {"rule_name": "a", "stage": "a", "alerts_created": 1, "duration_seconds": 0.01, "error": None},
                {"rule_name": "b", "stage": "b", "alerts_created": 2, "duration_seconds": 0.01, "error": None},
                {"rule_name": "c", "stage": "c", "alerts_created": 0, "duration_seconds": 0.01, "error": "boom"},
                {"rule_name": "d", "stage": "d", "alerts_created": 1, "duration_seconds": 0.01, "error": None},
                {"rule_name": "e", "stage": "e", "alerts_created": 0, "duration_seconds": 0.01, "error": None},
                {"rule_name": "f", "stage": "f", "alerts_created": 4, "duration_seconds": 0.01, "error": None},
            ],
        ):
            summary = rule_engine.run_once(cursor, "7 days", "TEST")

        assert summary["alerts_created"] == 8
        assert summary["rules_executed"] == 6
        assert summary["error_count"] == 1
