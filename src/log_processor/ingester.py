"""
Log ingestion pipeline: reads logs and inserts them into PostgreSQL.

Usage:
    python src/log_processor/ingester.py logs/app.log
    python src/log_processor/ingester.py logs/app.log --batch-size 500
    python src/log_processor/ingester.py /tmp/access.log --format apache_combined
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
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

SUPPORTED_FORMATS = ("json", "apache_combined", "auto")

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


def parse_json_log_line(line: str) -> Optional[dict]:
    """Parse one JSON log line."""
    try:
        payload = json.loads(line.strip())
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None
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


def parse_apache_combined_log_line(line: str) -> Optional[dict]:
    """Parse one Apache/Nginx access-log line close to the combined format."""
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
        "log_type": "apache_access",
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
        "source_format": "apache_combined",
    }


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
    elif log_format == "apache_combined":
        parsed = parse_apache_combined_log_line(stripped)
    else:
        parsers = [
            (parse_json_log_line, "json"),
            (parse_apache_combined_log_line, "apache_combined"),
        ]
        if not stripped.startswith("{"):
            parsers.reverse()

        parsed = None
        for parser, _parser_name in parsers:
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
            print(f"✓ Ingested {total_ingested} logs ({reason})")
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
