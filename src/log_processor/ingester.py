"""
Log ingestion pipeline: reads logs and inserts them into PostgreSQL.

Usage:
    python src/log_processor/ingester.py logs/app.log
    python src/log_processor/ingester.py logs/app.log --batch-size 500
    python src/log_processor/ingester.py /tmp/access.log --format apache_combined
    python src/log_processor/ingester.py /tmp/access.log --format nginx_combined
    python src/log_processor/ingester.py /tmp/access.json --format web_json
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.monitoring.metrics import observe_pipeline_stage, persist_component_runtime_metrics

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "database": os.getenv("POSTGRES_DB", "logmonitor"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", "changeme"),
}

SUPPORTED_FORMATS = (
    "json",
    "web_json",
    "apache_combined",
    "apache_common",
    "nginx_combined",
    "auto",
)

APACHE_ACCESS_PATTERN = re.compile(
    r'^(?P<ip>\S+)\s+'
    r'(?P<ident>\S+)\s+'
    r'(?P<authuser>\S+)\s+'
    r'\[(?P<timestamp>[^\]]+)\]\s+'
    r'"(?P<request>[^"]*)"\s+'
    r'(?P<status>\d{3}|-)\s+'
    r'(?P<body_bytes_sent>\d+|-)(?:\s+'
    r'"(?P<referrer>[^"]*)"\s+'
    r'"(?P<user_agent>[^"]*)")?'
    r'(?P<extra>.*)$'
)

TIMESTAMP_FIELD_CANDIDATES = ("timestamp", "@timestamp", "time", "time_iso8601", "time_local", "ts")
IP_FIELD_CANDIDATES = ("ip", "remote_addr", "client_ip", "x_forwarded_for", "http_x_forwarded_for")
METHOD_FIELD_CANDIDATES = ("method", "request_method", "http_method", "verb")
ENDPOINT_FIELD_CANDIDATES = ("endpoint", "path", "uri", "request_uri", "url")
STATUS_FIELD_CANDIDATES = ("status", "status_code", "response_status")
USER_AGENT_FIELD_CANDIDATES = ("user_agent", "http_user_agent", "agent")
REFERRER_FIELD_CANDIDATES = ("referrer", "http_referer", "referer")
BYTES_SENT_FIELD_CANDIDATES = ("bytes_sent", "body_bytes_sent", "response_size", "bytes")
REQUEST_LINE_FIELD_CANDIDATES = ("request", "request_line")
PROTOCOL_FIELD_CANDIDATES = ("protocol", "server_protocol")
RESPONSE_TIME_FIELD_CANDIDATES = (
    ("response_time_ms", "ms"),
    ("request_time_ms", "ms"),
    ("duration_ms", "ms"),
    ("latency_ms", "ms"),
    ("response_time", "auto"),
    ("request_time", "seconds"),
    ("upstream_response_time", "seconds"),
    ("duration", "auto"),
    ("latency", "auto"),
)
WEB_HINT_KEYS = set(
    METHOD_FIELD_CANDIDATES
    + ENDPOINT_FIELD_CANDIDATES
    + STATUS_FIELD_CANDIDATES
    + USER_AGENT_FIELD_CANDIDATES
    + REQUEST_LINE_FIELD_CANDIDATES
    + PROTOCOL_FIELD_CANDIDATES
    + tuple(name for name, _unit in RESPONSE_TIME_FIELD_CANDIDATES)
)


def get_db_connection(max_retries: int = 5):
    """Create database connection with retry logic."""
    for attempt in range(max_retries):
        try:
            return psycopg2.connect(**DB_CONFIG)
        except psycopg2.OperationalError:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"Connection failed (attempt {attempt + 1}/{max_retries})")
                print(f"Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"ERROR: Could not connect after {max_retries} attempts")
                raise


def _pick_first_present(payload: dict, candidates: tuple[str, ...]):
    for candidate in candidates:
        if candidate in payload and payload[candidate] not in (None, "", "-"):
            return payload[candidate]
    return None


def _normalize_text_value(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            normalized = _normalize_text_value(item)
            if normalized is not None:
                return normalized
        return None

    text = str(value).strip().strip('"').strip("'")
    if text == '-':
        return None
    return text or None


def _normalize_ip_value(value) -> Optional[str]:
    text = _normalize_text_value(value)
    if text is None:
        return None
    return text.split(',')[0].strip()


def _normalize_timestamp_value(value) -> Optional[str]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 1_000_000_000_000:
            numeric /= 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()

    text = _normalize_text_value(value)
    if text is None:
        return None

    iso_candidate = text.replace('Z', '+00:00')
    parsers = [
        lambda raw: datetime.fromisoformat(raw),
        lambda raw: datetime.strptime(raw, "%d/%b/%Y:%H:%M:%S %z"),
        lambda raw: datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S"),
        lambda raw: datetime.strptime(raw, "%Y-%m-%d %H:%M:%S"),
    ]
    for parser in parsers:
        try:
            parsed = parser(iso_candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            continue
    return text


def _normalize_status_value(value) -> Optional[int]:
    text = _normalize_text_value(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _normalize_bytes_value(value) -> Optional[int]:
    text = _normalize_text_value(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _normalize_response_time_value(value, unit: str) -> Optional[float]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        numeric_value = float(value)
        raw_text = str(value)
    else:
        raw_text = _normalize_text_value(value)
        if raw_text is None:
            return None
        match = re.search(r'-?\d+(?:\.\d+)?', raw_text)
        if match is None:
            return None
        numeric_value = float(match.group(0))

    if unit == "ms":
        return round(numeric_value, 3)
    if unit == "seconds":
        return round(numeric_value * 1000.0, 3)
    if '.' in raw_text and abs(numeric_value) <= 10:
        return round(numeric_value * 1000.0, 3)
    return round(numeric_value, 3)


def parse_json_log_line(line: str) -> Optional[dict]:
    """Parse one JSON log line."""
    try:
        payload = json.loads(line.strip())
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
    if WEB_HINT_KEYS.intersection(payload):
        return _normalize_web_json_payload(payload, source_format="json", default_log_type="web")
    return payload


def _parse_request_components(request_line: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Split an HTTP request line into method, endpoint and protocol."""
    if not request_line or request_line == "-":
        return None, None, None

    parts = request_line.split()
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], None
    return None, request_line, None


def _parse_optional_request_time_ms(extra_fields: str) -> Optional[float]:
    """Best-effort extraction of request duration from extended access-log formats."""
    for token in extra_fields.split():
        cleaned = token.strip('"')
        if not cleaned or cleaned == "-":
            continue

        try:
            numeric_value = float(cleaned)
        except ValueError:
            continue

        if "." in cleaned:
            return round(numeric_value * 1000, 3)
        if numeric_value >= 100000:
            return round(numeric_value / 1000, 3)
        return numeric_value

    return None


def _looks_like_web_payload(payload: dict) -> bool:
    return any(payload.get(field) is not None for field in ("method", "endpoint", "status", "request_line", "user_agent"))


def _normalize_web_json_payload(payload: dict, source_format: str, default_log_type: str) -> dict:
    normalized = dict(payload)

    request_line = _normalize_text_value(_pick_first_present(normalized, REQUEST_LINE_FIELD_CANDIDATES))
    method = _normalize_text_value(_pick_first_present(normalized, METHOD_FIELD_CANDIDATES))
    endpoint = _normalize_text_value(_pick_first_present(normalized, ENDPOINT_FIELD_CANDIDATES))
    protocol = _normalize_text_value(_pick_first_present(normalized, PROTOCOL_FIELD_CANDIDATES))

    if request_line and (method is None or endpoint is None or protocol is None):
        parsed_method, parsed_endpoint, parsed_protocol = _parse_request_components(request_line)
        method = method or parsed_method
        endpoint = endpoint or parsed_endpoint
        protocol = protocol or parsed_protocol

    timestamp = _normalize_timestamp_value(_pick_first_present(normalized, TIMESTAMP_FIELD_CANDIDATES))
    if timestamp is not None:
        normalized["timestamp"] = timestamp

    ip = _normalize_ip_value(_pick_first_present(normalized, IP_FIELD_CANDIDATES))
    if ip is not None:
        normalized["ip"] = ip

    if method is not None:
        normalized["method"] = method
    if endpoint is not None:
        normalized["endpoint"] = endpoint
    if protocol is not None:
        normalized["protocol"] = protocol

    status = _normalize_status_value(_pick_first_present(normalized, STATUS_FIELD_CANDIDATES))
    if status is not None:
        normalized["status"] = status

    user_agent = _normalize_text_value(_pick_first_present(normalized, USER_AGENT_FIELD_CANDIDATES))
    if user_agent is not None:
        normalized["user_agent"] = user_agent

    referrer = _normalize_text_value(_pick_first_present(normalized, REFERRER_FIELD_CANDIDATES))
    if referrer is not None:
        normalized["referrer"] = referrer

    bytes_sent = _normalize_bytes_value(_pick_first_present(normalized, BYTES_SENT_FIELD_CANDIDATES))
    if bytes_sent is not None:
        normalized["bytes_sent"] = bytes_sent

    for field_name, unit in RESPONSE_TIME_FIELD_CANDIDATES:
        if field_name in normalized and normalized[field_name] not in (None, "", "-"):
            response_time_ms = _normalize_response_time_value(normalized[field_name], unit)
            if response_time_ms is not None:
                normalized["response_time_ms"] = response_time_ms
            break

    if request_line is not None:
        normalized["request_line"] = request_line

    if _looks_like_web_payload(normalized):
        normalized.setdefault("source_format", source_format)
        normalized.setdefault("log_type", default_log_type)
    return normalized


def parse_web_json_log_line(line: str) -> Optional[dict]:
    """Parse structured JSON access logs from web servers or proxies."""
    try:
        payload = json.loads(line.strip())
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    normalized = _normalize_web_json_payload(payload, source_format="web_json", default_log_type="web_access")
    return normalized if _looks_like_web_payload(normalized) else None


def parse_access_log_line(
    line: str,
    *,
    source_format: str = "apache_combined",
    default_log_type: str = "apache_access",
) -> Optional[dict]:
    """Parse one Apache/Nginx access-log line close to the combined/common format."""
    match = APACHE_ACCESS_PATTERN.match(line.strip())
    if not match:
        return None

    groups = match.groupdict()
    method, endpoint, protocol = _parse_request_components(groups["request"])

    try:
        timestamp = datetime.strptime(groups["timestamp"], "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None

    status_value = groups["status"]
    bytes_sent_value = groups["body_bytes_sent"]

    return {
        "log_type": default_log_type,
        "timestamp": timestamp.isoformat(),
        "ip": None if groups["ip"] == "-" else groups["ip"],
        "method": method,
        "endpoint": endpoint,
        "status": None if status_value == "-" else int(status_value),
        "response_time_ms": _parse_optional_request_time_ms(groups.get("extra", "")),
        "user_agent": None if groups.get("user_agent") in (None, "-") else groups["user_agent"],
        "protocol": protocol,
        "bytes_sent": None if bytes_sent_value == "-" else int(bytes_sent_value),
        "referrer": None if groups.get("referrer") in (None, "-") else groups["referrer"],
        "request_line": groups["request"],
        "source_format": source_format,
    }


def parse_apache_combined_log_line(line: str) -> Optional[dict]:
    """Backwards-compatible wrapper for Apache combined logs."""
    return parse_access_log_line(line, source_format="apache_combined", default_log_type="apache_access")


def parse_log_line(line: str, log_format: str = "json") -> Optional[dict]:
    """Parse a log line according to the requested source format."""
    stripped = line.strip()
    if not stripped:
        return None

    if log_format not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported log format '{log_format}'. Supported values: {', '.join(SUPPORTED_FORMATS)}"
        )

    if log_format == "json":
        parsed = parse_json_log_line(stripped)
    elif log_format == "web_json":
        parsed = parse_web_json_log_line(stripped)
    elif log_format == "apache_combined":
        parsed = parse_access_log_line(stripped, source_format="apache_combined", default_log_type="apache_access")
    elif log_format == "apache_common":
        parsed = parse_access_log_line(stripped, source_format="apache_common", default_log_type="apache_access")
    elif log_format == "nginx_combined":
        parsed = parse_access_log_line(stripped, source_format="nginx_combined", default_log_type="nginx_access")
    else:
        parsers = [
            lambda raw: parse_web_json_log_line(raw),
            lambda raw: parse_json_log_line(raw),
            lambda raw: parse_access_log_line(raw, source_format="combined_access", default_log_type="web_access"),
        ]
        if not stripped.startswith("{"):
            parsers = [parsers[2], parsers[0], parsers[1]]

        parsed = None
        for parser in parsers:
            parsed = parser(stripped)
            if parsed is not None:
                break

    if parsed is None:
        print(f"WARNING: Failed to parse line as {log_format}: {stripped[:140]}...")
    return parsed


def prepare_log_for_insert(log: dict) -> tuple:
    """Extract fields from log dict for INSERT."""
    timestamp = log.get("timestamp")

    if timestamp is None:
        timestamp = datetime.now()

    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            try:
                timestamp = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                print(f"WARNING: Invalid timestamp format: {timestamp}")
                timestamp = datetime.now()
    elif not isinstance(timestamp, datetime):
        timestamp = datetime.now()

    return (
        log.get("log_type", "web"),
        timestamp,
        log.get("ip"),
        log.get("method"),
        log.get("endpoint"),
        log.get("status"),
        log.get("response_time_ms"),
        log.get("user_agent"),
        json.dumps(log),
    )


def insert_logs_batch(cursor, logs: list[dict]):
    """Insert batch of logs using execute_values (fast)."""
    if not logs:
        return 0

    query = """
        INSERT INTO raw_logs (
            log_type, timestamp, ip, method, endpoint,
            status, response_time_ms, user_agent, data
        ) VALUES %s
    """

    prepare_started = time.perf_counter()
    data = [prepare_log_for_insert(log) for log in logs]
    prepare_duration = time.perf_counter() - prepare_started
    observe_pipeline_stage(
        "ingester",
        "prepare_insert",
        prepare_duration,
        batch_size=len(data),
        row_count=len(data),
    )

    execute_started = time.perf_counter()
    try:
        execute_values(cursor, query, data, page_size=len(data))
    except Exception:
        observe_pipeline_stage(
            "ingester",
            "execute_values",
            time.perf_counter() - execute_started,
            batch_size=len(data),
            row_count=0,
            error_count=1,
        )
        raise

    execute_duration = time.perf_counter() - execute_started
    observe_pipeline_stage(
        "ingester",
        "execute_values",
        execute_duration,
        batch_size=len(data),
        row_count=len(data),
    )
    return len(data)


def ingest_from_file(filepath: str, batch_size: int = 100, log_format: str = "json"):
    """Ingest logs from file in batches."""
    if not os.path.exists(filepath):
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    print(f"Starting ingestion from: {filepath}")
    print(f"Batch size: {batch_size}")
    print(f"Log format: {log_format}")
    print(f"Connecting to PostgreSQL at {DB_CONFIG['host']}:{DB_CONFIG['port']}")

    conn = get_db_connection()
    cursor = conn.cursor()

    batch: list[dict] = []
    total_ingested = 0
    total_failed = 0
    total_lines = 0
    batch_parse_duration = 0.0
    batch_lines_seen = 0
    start_time = time.perf_counter()

    def flush_batch(reason: str):
        nonlocal batch, total_ingested, total_failed, batch_parse_duration, batch_lines_seen
        if not batch:
            return

        observe_pipeline_stage(
            "ingester",
            "parse_batch",
            batch_parse_duration,
            batch_size=len(batch),
            row_count=batch_lines_seen,
        )

        insert_started = time.perf_counter()
        try:
            inserted = insert_logs_batch(cursor, batch)
            insert_duration = time.perf_counter() - insert_started
            observe_pipeline_stage(
                "ingester",
                "insert_batch_total",
                insert_duration,
                batch_size=len(batch),
                row_count=inserted,
            )

            commit_started = time.perf_counter()
            conn.commit()
            commit_duration = time.perf_counter() - commit_started
            observe_pipeline_stage(
                "ingester",
                "commit_batch",
                commit_duration,
                batch_size=len(batch),
                row_count=inserted,
            )

            total_ingested += inserted
            print(f"âœ“ Ingested {total_ingested} logs ({reason})")
        except Exception as exc:
            conn.rollback()
            observe_pipeline_stage(
                "ingester",
                "insert_batch_total",
                time.perf_counter() - insert_started,
                batch_size=len(batch),
                row_count=0,
                error_count=1,
            )
            print(f"ERROR inserting batch ({reason}): {exc}")
            total_failed += len(batch)
        finally:
            batch = []
            batch_parse_duration = 0.0
            batch_lines_seen = 0

    try:
        with open(filepath, "r", encoding="utf-8") as handle:
            for line_num, line in enumerate(handle, 1):
                total_lines += 1
                parse_started = time.perf_counter()
                log = parse_log_line(line, log_format=log_format)
                batch_parse_duration += time.perf_counter() - parse_started
                batch_lines_seen += 1

                if log:
                    batch.append(log)
                else:
                    total_failed += 1

                if len(batch) >= batch_size:
                    flush_batch(f"line {line_num}")

        if batch:
            flush_batch("final batch")

        elapsed = time.perf_counter() - start_time
        rate = total_ingested / elapsed if elapsed > 0 else 0.0

        persist_component_runtime_metrics(
            "ingester",
            {
                "source_file": filepath,
                "log_format": log_format,
                "configured_batch_size": batch_size,
                "lines_read": total_lines,
                "logs_ingested": total_ingested,
                "failed_records": total_failed,
                "duration_seconds": round(elapsed, 6),
                "throughput_logs_per_second": round(rate, 6),
            },
        )

        print("\n" + "=" * 50)
        print("INGESTION COMPLETE")
        print("=" * 50)
        print(f"Total ingested: {total_ingested}")
        print(f"Total failed:   {total_failed}")
        print(f"Time elapsed:   {elapsed:.2f}s")
        print(f"Ingestion rate: {rate:.0f} logs/second")

    except KeyboardInterrupt:
        print("\n\nIngestion interrupted by user")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Ingest logs into PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ingester.py logs/app.log
  python ingester.py logs/app.log --batch-size 500
  python ingester.py logs/production.log --batch-size 1000
  python ingester.py /tmp/access.log --format apache_combined
  python ingester.py /tmp/access.log --format nginx_combined
  python ingester.py /tmp/access.json --format web_json
  python ingester.py /tmp/access.log --format nginx_combined
  python ingester.py /tmp/access.json --format web_json
        """,
    )

    parser.add_argument("logfile", help="Path to the log file")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of logs to insert per batch (default: 100)",
    )
    parser.add_argument(
        "--format",
        choices=SUPPORTED_FORMATS,
        default="json",
        help="Source log format (default: json)",
    )

    args = parser.parse_args()
    ingest_from_file(args.logfile, args.batch_size, log_format=args.format)


if __name__ == "__main__":
    main()

