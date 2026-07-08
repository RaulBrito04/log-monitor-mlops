from __future__ import annotations

import json
from datetime import datetime

import psycopg2
import pytest

from src.log_processor import ingester


class TestLogParser:
    def test_parse_log_line_valid_json(self):
        line = json.dumps({"timestamp": "2025-01-01T12:00:00+00:00", "status": 200})
        assert ingester.parse_log_line(line)["status"] == 200

    def test_parse_log_line_invalid_json_returns_none(self):
        assert ingester.parse_log_line("{invalid json") is None

    def test_parse_apache_combined_line_extracts_http_fields(self):
        line = (
            '192.168.1.10 - - [10/Oct/2000:13:55:36 -0700] '
            '"GET /api/items?id=1 HTTP/1.1" 200 2326 '
            '"https://example.com" "Mozilla/5.0" 0.123'
        )

        parsed = ingester.parse_apache_combined_log_line(line)

        assert parsed is not None
        assert parsed["log_type"] == "apache_access"
        assert parsed["method"] == "GET"
        assert parsed["endpoint"] == "/api/items?id=1"
        assert parsed["status"] == 200
        assert parsed["bytes_sent"] == 2326
        assert parsed["response_time_ms"] == pytest.approx(123.0)
        assert parsed["source_format"] == "apache_combined"

    def test_parse_apache_common_line_extracts_http_fields(self):
        line = '10.0.0.4 - - [27/Jun/2026:14:03:20 +0000] "GET /health HTTP/1.1" 200 321'

        parsed = ingester.parse_log_line(line, log_format="apache_common")

        assert parsed is not None
        assert parsed["log_type"] == "apache_access"
        assert parsed["endpoint"] == "/health"
        assert parsed["status"] == 200
        assert parsed["user_agent"] is None
        assert parsed["source_format"] == "apache_common"

    def test_parse_nginx_combined_line_extracts_http_fields(self):
        line = (
            '10.0.0.8 - - [27/Jun/2026:14:03:20 +0000] '
            '"POST /login HTTP/1.1" 401 512 "-" "curl/8.0" 0.250'
        )

        parsed = ingester.parse_log_line(line, log_format="nginx_combined")

        assert parsed is not None
        assert parsed["log_type"] == "nginx_access"
        assert parsed["method"] == "POST"
        assert parsed["endpoint"] == "/login"
        assert parsed["status"] == 401
        assert parsed["response_time_ms"] == pytest.approx(250.0)
        assert parsed["source_format"] == "nginx_combined"

    def test_parse_web_json_line_normalizes_nginx_style_fields(self):
        line = json.dumps(
            {
                "time_local": "27/Jun/2026:14:03:21 +0000",
                "remote_addr": "10.0.0.2",
                "request": "POST /login HTTP/1.1",
                "status": 401,
                "body_bytes_sent": 512,
                "request_time": 0.250,
                "http_user_agent": "Mozilla/5.0",
                "http_referer": "-",
            }
        )

        parsed = ingester.parse_log_line(line, log_format="web_json")

        assert parsed is not None
        assert parsed["log_type"] == "web_access"
        assert parsed["timestamp"].startswith("2026-06-27T14:03:21")
        assert parsed["ip"] == "10.0.0.2"
        assert parsed["method"] == "POST"
        assert parsed["endpoint"] == "/login"
        assert parsed["protocol"] == "HTTP/1.1"
        assert parsed["status"] == 401
        assert parsed["bytes_sent"] == 512
        assert parsed["response_time_ms"] == pytest.approx(250.0)
        assert parsed["source_format"] == "web_json"

    def test_parse_log_line_auto_detects_apache_access_logs(self):
        line = (
            '10.0.0.8 - - [27/Jun/2026:14:03:20 +0000] '
            '"POST /login HTTP/1.1" 401 512 "-" "curl/8.0"'
        )

        parsed = ingester.parse_log_line(line, log_format="auto")

        assert parsed is not None
        assert parsed["method"] == "POST"
        assert parsed["endpoint"] == "/login"
        assert parsed["status"] == 401

    def test_parse_log_line_auto_detects_structured_web_json(self):
        line = json.dumps(
            {
                "time_iso8601": "2026-06-27T14:03:21+00:00",
                "remote_addr": "10.0.0.2",
                "request": "GET /health HTTP/1.1",
                "status": 200,
                "request_time": 0.031,
                "http_user_agent": "curl/8.0",
            }
        )

        parsed = ingester.parse_log_line(line, log_format="auto")

        assert parsed is not None
        assert parsed["endpoint"] == "/health"
        assert parsed["status"] == 200
        assert parsed["response_time_ms"] == pytest.approx(31.0)

    def test_parse_log_line_invalid_format_raises(self):
        with pytest.raises(ValueError):
            ingester.parse_log_line("{}", log_format="syslog")


class TestPrepareLogForInsert:
    def test_prepare_log_for_insert_converts_iso_timestamp(self):
        row = ingester.prepare_log_for_insert(
            {
                "timestamp": "2025-01-01T12:00:00+00:00",
                "ip": "127.0.0.1",
                "method": "GET",
                "endpoint": "/health",
                "status": 200,
                "response_time_ms": 10,
                "user_agent": "pytest",
            }
        )

        assert isinstance(row[1], datetime)
        assert row[2] == "127.0.0.1"
        assert row[4] == "/health"

    def test_prepare_log_for_insert_defaults_missing_timestamp(self):
        row = ingester.prepare_log_for_insert(
            {
                "ip": "127.0.0.1",
                "method": "GET",
                "endpoint": "/health",
                "status": 200,
                "response_time_ms": 10,
                "user_agent": "pytest",
            }
        )

        assert isinstance(row[1], datetime)


class TestBatchInsert:
    def test_insert_logs_batch_uses_execute_values(self, mocker):
        execute_values = mocker.patch("src.log_processor.ingester.execute_values")
        cursor = mocker.Mock()
        logs = [
            {
                "timestamp": "2025-01-01T12:00:00+00:00",
                "ip": "127.0.0.1",
                "method": "GET",
                "endpoint": "/health",
                "status": 200,
                "response_time_ms": 10,
                "user_agent": "pytest",
            }
        ]

        inserted = ingester.insert_logs_batch(cursor, logs)

        assert inserted == 1
        execute_values.assert_called_once()

    def test_insert_logs_batch_copy_uses_copy_expert(self, mocker):
        cursor = mocker.Mock()
        logs = [
            {
                "timestamp": "2025-01-01T12:00:00+00:00",
                "ip": "127.0.0.1",
                "method": "GET",
                "endpoint": "/health",
                "status": 200,
                "response_time_ms": 10,
                "user_agent": "pytest",
            }
        ]

        inserted = ingester.insert_logs_batch_copy(cursor, logs)

        assert inserted == 1
        cursor.copy_expert.assert_called_once()
        copy_sql, buffer = cursor.copy_expert.call_args.args
        assert "COPY raw_logs" in copy_sql
        copied_payload = buffer.getvalue()
        assert "/health" in copied_payload
        assert "127.0.0.1" in copied_payload

    def test_insert_logs_batch_with_method_rejects_unknown_method(self):
        with pytest.raises(ValueError, match="Unsupported insert method"):
            ingester.insert_logs_batch_with_method(None, [], insert_method="unknown")

    def test_get_db_connection_retries_then_succeeds(self, mocker):
        conn = mocker.Mock()
        connect = mocker.patch(
            "src.log_processor.ingester.psycopg2.connect",
            side_effect=[psycopg2.OperationalError("down"), conn],
        )
        sleep = mocker.patch("src.log_processor.ingester.time.sleep")

        result = ingester.get_db_connection(max_retries=2)

        assert result is conn
        assert connect.call_count == 2
        sleep.assert_called_once()

    def test_ingest_from_file_processes_json_batches(self, tmp_path, mocker):
        logfile = tmp_path / "sample.log"
        logfile.write_text(
            "\n".join(
                [
                    json.dumps({"timestamp": "2025-01-01T12:00:00+00:00", "ip": "127.0.0.1", "method": "GET", "endpoint": "/health", "status": 200, "response_time_ms": 10, "user_agent": "pytest"}),
                    json.dumps({"timestamp": "2025-01-01T12:00:01+00:00", "ip": "127.0.0.2", "method": "GET", "endpoint": "/api/data", "status": 200, "response_time_ms": 11, "user_agent": "pytest"}),
                    "not-json",
                ]
            ),
            encoding="utf-8",
        )

        cursor = mocker.Mock()
        conn = mocker.Mock()
        conn.cursor.return_value = cursor
        mocker.patch("src.log_processor.ingester.get_db_connection", return_value=conn)
        mocker.patch("src.log_processor.ingester.persist_component_runtime_metrics")
        insert_batch = mocker.patch(
            "src.log_processor.ingester.insert_logs_batch",
            side_effect=lambda _cursor, batch: len(batch),
        )

        ingester.ingest_from_file(str(logfile), batch_size=2)

        assert insert_batch.call_count == 1
        conn.commit.assert_called_once()

    def test_ingest_from_file_processes_apache_batches(self, tmp_path, mocker):
        logfile = tmp_path / "access.log"
        logfile.write_text(
            "\n".join(
                [
                    '10.0.0.1 - - [27/Jun/2026:14:03:20 +0000] "GET /health HTTP/1.1" 200 321 "-" "curl/8.0"',
                    '10.0.0.2 - - [27/Jun/2026:14:03:21 +0000] "POST /login HTTP/1.1" 401 512 "-" "Mozilla/5.0" 0.250',
                    'bad apache line',
                ]
            ),
            encoding="utf-8",
        )

        cursor = mocker.Mock()
        conn = mocker.Mock()
        conn.cursor.return_value = cursor
        mocker.patch("src.log_processor.ingester.get_db_connection", return_value=conn)
        mocker.patch("src.log_processor.ingester.persist_component_runtime_metrics")
        insert_batch = mocker.patch(
            "src.log_processor.ingester.insert_logs_batch",
            side_effect=lambda _cursor, batch: len(batch),
        )

        ingester.ingest_from_file(
            str(logfile),
            batch_size=2,
            log_format="apache_combined",
        )

        assert insert_batch.call_count == 1
        apache_batch = insert_batch.call_args[0][1]
        assert apache_batch[0]["endpoint"] == "/health"
        assert apache_batch[1]["response_time_ms"] == pytest.approx(250.0)
        conn.commit.assert_called_once()

    def test_ingest_from_file_processes_web_json_batches(self, tmp_path, mocker):
        logfile = tmp_path / "access.json"
        logfile.write_text(
            "\n".join(
                [
                    json.dumps({"time_iso8601": "2026-06-27T14:03:20+00:00", "remote_addr": "10.0.0.1", "request": "GET /health HTTP/1.1", "status": 200, "request_time": 0.031, "http_user_agent": "curl/8.0"}),
                    json.dumps({"time_local": "27/Jun/2026:14:03:21 +0000", "remote_addr": "10.0.0.2", "request": "POST /login HTTP/1.1", "status": 401, "body_bytes_sent": 512, "request_time": 0.250, "http_user_agent": "Mozilla/5.0"}),
                    json.dumps({"message": "not an access log"}),
                ]
            ),
            encoding="utf-8",
        )

        cursor = mocker.Mock()
        conn = mocker.Mock()
        conn.cursor.return_value = cursor
        mocker.patch("src.log_processor.ingester.get_db_connection", return_value=conn)
        mocker.patch("src.log_processor.ingester.persist_component_runtime_metrics")
        insert_batch = mocker.patch(
            "src.log_processor.ingester.insert_logs_batch",
            side_effect=lambda _cursor, batch: len(batch),
        )

        ingester.ingest_from_file(
            str(logfile),
            batch_size=2,
            log_format="web_json",
        )

        assert insert_batch.call_count == 1
        web_batch = insert_batch.call_args[0][1]
        assert web_batch[0]["endpoint"] == "/health"
        assert web_batch[1]["response_time_ms"] == pytest.approx(250.0)
        assert web_batch[1]["source_format"] == "web_json"
        conn.commit.assert_called_once()

    def test_ingest_from_file_missing_path_exits(self):
        with pytest.raises(SystemExit):
            ingester.ingest_from_file("/tmp/does-not-exist.log")


    def test_ingest_from_file_processes_json_batches_with_copy(self, tmp_path, mocker):
        logfile = tmp_path / "sample-copy.log"
        logfile.write_text(
            "\n".join(
                [
                    json.dumps({"timestamp": "2025-01-01T12:00:00+00:00", "ip": "127.0.0.1", "method": "GET", "endpoint": "/health", "status": 200, "response_time_ms": 10, "user_agent": "pytest"}),
                    json.dumps({"timestamp": "2025-01-01T12:00:01+00:00", "ip": "127.0.0.2", "method": "GET", "endpoint": "/api/data", "status": 200, "response_time_ms": 11, "user_agent": "pytest"}),
                ]
            ),
            encoding="utf-8",
        )

        cursor = mocker.Mock()
        conn = mocker.Mock()
        conn.cursor.return_value = cursor
        mocker.patch("src.log_processor.ingester.get_db_connection", return_value=conn)
        mocker.patch("src.log_processor.ingester.persist_component_runtime_metrics")
        insert_batch = mocker.patch(
            "src.log_processor.ingester.insert_logs_batch_with_method",
            side_effect=lambda _cursor, batch, insert_method="execute_values": len(batch),
        )

        ingester.ingest_from_file(str(logfile), batch_size=2, insert_method="copy")

        assert insert_batch.call_count == 1
        assert insert_batch.call_args.kwargs["insert_method"] == "copy"
        conn.commit.assert_called_once()
