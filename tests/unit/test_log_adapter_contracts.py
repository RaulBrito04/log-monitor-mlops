from __future__ import annotations

import json

import pytest

from src.log_processor import ingester


@pytest.mark.parametrize(
    ("log_format", "line", "expected"),
    [
        (
            "apache_combined",
            '192.168.1.10 - - [10/Oct/2000:13:55:36 -0700] "GET /api/items?id=1 HTTP/1.1" 200 2326 "https://example.com" "Mozilla/5.0" 0.123',
            {
                "log_type": "apache_access",
                "source_format": "apache_combined",
                "method": "GET",
                "endpoint": "/api/items?id=1",
                "status": 200,
            },
        ),
        (
            "apache_common",
            '10.0.0.4 - - [27/Jun/2026:14:03:20 +0000] "GET /health HTTP/1.1" 200 321',
            {
                "log_type": "apache_access",
                "source_format": "apache_common",
                "method": "GET",
                "endpoint": "/health",
                "status": 200,
            },
        ),
        (
            "nginx_combined",
            '10.0.0.8 - - [27/Jun/2026:14:03:20 +0000] "POST /login HTTP/1.1" 401 512 "-" "curl/8.0" 0.250',
            {
                "log_type": "nginx_access",
                "source_format": "nginx_combined",
                "method": "POST",
                "endpoint": "/login",
                "status": 401,
            },
        ),
        (
            "web_json",
            json.dumps(
                {
                    "time_local": "27/Jun/2026:14:03:21 +0000",
                    "remote_addr": "10.0.0.2",
                    "request": "POST /login HTTP/1.1",
                    "status": 401,
                    "body_bytes_sent": 512,
                    "request_time": 0.250,
                    "http_user_agent": "Mozilla/5.0",
                }
            ),
            {
                "log_type": "web_access",
                "source_format": "web_json",
                "method": "POST",
                "endpoint": "/login",
                "status": 401,
            },
        ),
    ],
)
def test_supported_log_adapters_emit_normalized_contract(log_format, line, expected):
    parsed = ingester.parse_log_line(line, log_format=log_format)

    assert parsed is not None
    assert isinstance(parsed["timestamp"], str)
    assert parsed["ip"]
    assert parsed["method"] == expected["method"]
    assert parsed["endpoint"] == expected["endpoint"]
    assert parsed["status"] == expected["status"]
    assert parsed["log_type"] == expected["log_type"]
    assert parsed["source_format"] == expected["source_format"]


def test_auto_adapter_contract_detects_structured_json_and_access_logs():
    structured = json.dumps(
        {
            "time_iso8601": "2026-06-27T14:03:21+00:00",
            "remote_addr": "10.0.0.2",
            "request": "GET /health HTTP/1.1",
            "status": 200,
            "request_time": 0.031,
            "http_user_agent": "curl/8.0",
        }
    )
    access = '10.0.0.8 - - [27/Jun/2026:14:03:20 +0000] "POST /login HTTP/1.1" 401 512 "-" "curl/8.0"'

    parsed_structured = ingester.parse_log_line(structured, log_format="auto")
    parsed_access = ingester.parse_log_line(access, log_format="auto")

    assert parsed_structured is not None
    assert parsed_structured["source_format"] == "web_json"
    assert parsed_structured["endpoint"] == "/health"
    assert parsed_access is not None
    assert parsed_access["source_format"] == "combined_access"
    assert parsed_access["endpoint"] == "/login"


def test_web_json_contract_prefers_first_forwarded_ip_and_request_line_fallback():
    parsed = ingester.parse_log_line(
        json.dumps(
            {
                "timestamp": "2026-06-27T14:03:21+00:00",
                "x_forwarded_for": "10.0.0.5, 10.0.0.6",
                "request": "DELETE /api/items/7 HTTP/2",
                "status_code": 204,
                "duration_ms": 12,
            }
        ),
        log_format="web_json",
    )

    assert parsed is not None
    assert parsed["ip"] == "10.0.0.5"
    assert parsed["method"] == "DELETE"
    assert parsed["endpoint"] == "/api/items/7"
    assert parsed["protocol"] == "HTTP/2"
    assert parsed["response_time_ms"] == pytest.approx(12.0)


def test_web_json_contract_normalizes_seconds_to_milliseconds():
    parsed = ingester.parse_log_line(
        json.dumps(
            {
                "time_iso8601": "2026-06-27T14:03:21+00:00",
                "remote_addr": "10.0.0.9",
                "request": "GET /metrics HTTP/1.1",
                "status": 200,
                "request_time": 0.004,
            }
        ),
        log_format="web_json",
    )

    assert parsed is not None
    assert parsed["response_time_ms"] == pytest.approx(4.0)
