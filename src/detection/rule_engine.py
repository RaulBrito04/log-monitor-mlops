"""
Rule Engine - Detecao baseada em regras SQL
6 regras: brute force, sql injection, port scanning,
          path traversal, suspicious user agent, time-based anomaly

Modos:
  --mode historical   Analisa todo o historico (corre uma vez)
  --mode realtime     Loop continuo com janela de 60 segundos (producao)

Uso:
  python src/detection/rule_engine.py --mode historical
  python src/detection/rule_engine.py --mode historical --days 30
  python src/detection/rule_engine.py --mode realtime
  python src/detection/rule_engine.py --mode realtime --interval 30
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.monitoring.metrics import observe_pipeline_stage, persist_component_runtime_metrics

load_dotenv()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_window(default: str = "60 seconds") -> str:
    value = os.getenv("RULE_ENGINE_WINDOW")
    if value is None or not value.strip():
        return default
    return value.strip()


def get_rules(window):
    """
    Retorna as 6 regras SQL com a janela de tempo fornecida.
    window: string PostgreSQL interval, ex: '60 seconds' ou '7 days'
    """
    return [
        (
            "Brute Force Detection",
            """
            INSERT INTO alerts (alert_type, severity, source, confidence,
                                description, log_ids, ip, timestamp, metadata)
            SELECT
                'brute_force', 'HIGH', 'rule', 1.0,
                'Detected ' || COUNT(*) || ' failed login attempts from IP ' || ip,
                ARRAY_AGG(id ORDER BY timestamp),
                ip,
                MAX(timestamp),
                jsonb_build_object(
                    'failed_attempts',    COUNT(*),
                    'endpoints_targeted', ARRAY_AGG(DISTINCT endpoint),
                    'time_window',        '{window}',
                    'first_attempt',      MIN(timestamp),
                    'last_attempt',       MAX(timestamp)
                )
            FROM raw_logs
            WHERE endpoint = '/login'
              AND status IN (401, 429)
              AND timestamp > NOW() - INTERVAL '{window}'
            GROUP BY ip
            HAVING COUNT(*) >= 5;
            """.replace("{window}", window),
        ),
        (
            "SQL Injection Detection",
            """
            INSERT INTO alerts (alert_type, severity, source, confidence,
                                description, log_ids, ip, timestamp, metadata)
            SELECT
                'sql_injection', 'CRITICAL', 'rule', 1.0,
                'SQL injection attempt from IP ' || ip || ' on ' || endpoint,
                ARRAY_AGG(id ORDER BY timestamp),
                ip,
                MAX(timestamp),
                jsonb_build_object(
                    'endpoint', endpoint,
                    'method',   method,
                    'attempts', COUNT(*)
                )
            FROM raw_logs
            WHERE timestamp > NOW() - INTERVAL '{window}'
              AND (
                  endpoint ILIKE '%union%select%'
               OR endpoint ILIKE '%or%1=1%'
               OR endpoint ILIKE '%drop%table%'
               OR endpoint ILIKE '%--%'
              )
            GROUP BY ip, endpoint, method
            HAVING COUNT(*) >= 1;
            """.replace("{window}", window),
        ),
        (
            "Port Scanning Detection",
            """
            INSERT INTO alerts (alert_type, severity, source, confidence,
                                description, log_ids, ip, timestamp, metadata)
            SELECT
                'port_scanning', 'MEDIUM', 'rule', 0.9,
                'Scanning: ' || COUNT(DISTINCT endpoint) || ' endpoints from IP ' || ip,
                ARRAY_AGG(id ORDER BY timestamp),
                ip,
                MAX(timestamp),
                jsonb_build_object(
                    'unique_endpoints',    COUNT(DISTINCT endpoint),
                    'total_requests',      COUNT(*),
                    'endpoints_list',      ARRAY_AGG(DISTINCT endpoint),
                    'time_window',         '{window}',
                    'average_response_ms', AVG(response_time_ms)
                )
            FROM raw_logs
            WHERE timestamp > NOW() - INTERVAL '{window}'
            GROUP BY ip
            HAVING COUNT(DISTINCT endpoint) >= 10;
            """.replace("{window}", window),
        ),
        (
            "Path Traversal Detection",
            """
            INSERT INTO alerts (alert_type, severity, source, confidence,
                                description, log_ids, ip, timestamp, metadata)
            SELECT
                'path_traversal', 'CRITICAL', 'rule', 1.0,
                'Path traversal attempt from IP ' || ip || ' on ' || endpoint,
                ARRAY_AGG(id ORDER BY timestamp),
                ip,
                MAX(timestamp),
                jsonb_build_object(
                    'endpoint',  endpoint,
                    'method',    method,
                    'attempts',  COUNT(*),
                    'pattern',   CASE
                        WHEN endpoint ILIKE '%../%'         THEN 'directory traversal'
                        WHEN endpoint ILIKE '%/etc/passwd%' THEN 'passwd file access'
                        WHEN endpoint ILIKE '%/etc/shadow%' THEN 'shadow file access'
                        WHEN endpoint ILIKE '%/proc/%'      THEN 'proc filesystem access'
                        ELSE 'other'
                    END
                )
            FROM raw_logs
            WHERE timestamp > NOW() - INTERVAL '{window}'
              AND (
                  endpoint ILIKE '%../%'
               OR endpoint ILIKE '%/etc/passwd%'
               OR endpoint ILIKE '%/etc/shadow%'
               OR endpoint ILIKE '%/proc/%'
               OR endpoint ILIKE '%/var/log/%'
              )
            GROUP BY ip, endpoint, method
            HAVING COUNT(*) >= 1;
            """.replace("{window}", window),
        ),
        (
            "Suspicious User Agent",
            """
            INSERT INTO alerts (alert_type, severity, source, confidence,
                                description, log_ids, ip, timestamp, metadata)
            SELECT
                'suspicious_user_agent', 'MEDIUM', 'rule', 0.85,
                'Suspicious tool detected from IP ' || ip,
                ARRAY_AGG(id ORDER BY timestamp),
                ip,
                MAX(timestamp),
                jsonb_build_object(
                    'user_agent', data->>'user_agent',
                    'requests',   COUNT(*),
                    'endpoints',  ARRAY_AGG(DISTINCT endpoint)
                )
            FROM raw_logs
            WHERE timestamp > NOW() - INTERVAL '{window}'
              AND (
                  data->>'user_agent' ILIKE '%sqlmap%'
               OR data->>'user_agent' ILIKE '%nikto%'
               OR data->>'user_agent' ILIKE '%nmap%'
               OR data->>'user_agent' ILIKE '%masscan%'
               OR data->>'user_agent' ILIKE '%zgrab%'
               OR data->>'user_agent' ILIKE '%python-requests%'
               OR data->>'user_agent' ILIKE '%go-http-client%'
               OR data->>'user_agent' ILIKE '%curl%'
              )
            GROUP BY ip, data->>'user_agent'
            HAVING COUNT(*) >= 3;
            """.replace("{window}", window),
        ),
        (
            "Time-Based Anomaly",
            """
            INSERT INTO alerts (alert_type, severity, source, confidence,
                                description, log_ids, ip, timestamp, metadata)
            SELECT
                'time_anomaly', 'LOW', 'rule', 0.7,
                'Off-hours activity: ' || COUNT(*) || ' requests from IP ' || ip
                    || ' between 22h-6h',
                ARRAY_AGG(id ORDER BY timestamp),
                ip,
                MAX(timestamp),
                jsonb_build_object(
                    'requests',          COUNT(*),
                    'unique_endpoints',  COUNT(DISTINCT endpoint),
                    'hours_active',      ARRAY_AGG(DISTINCT EXTRACT(HOUR FROM timestamp)::int),
                    'time_window',       '{window}'
                )
            FROM raw_logs
            WHERE timestamp > NOW() - INTERVAL '{window}'
              AND (
                  EXTRACT(HOUR FROM timestamp) >= 22
               OR EXTRACT(HOUR FROM timestamp) < 6
              )
            GROUP BY ip
            HAVING COUNT(*) >= 20;
            """.replace("{window}", window),
        ),
    ]


def _stage_name(rule_name: str) -> str:
    return rule_name.lower().replace(" ", "_")


def execute_rule(cursor, rule_name, sql_query):
    started = time.perf_counter()
    stage_name = _stage_name(rule_name)
    try:
        cursor.execute(sql_query)
        count = max(cursor.rowcount, 0)
    except Exception as exc:
        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "rule_engine",
            stage_name,
            duration,
            batch_size=1,
            row_count=0,
            error_count=1,
        )
        print(f"  ERRO {rule_name}: {exc}")
        return {
            "rule_name": rule_name,
            "stage": stage_name,
            "alerts_created": 0,
            "duration_seconds": round(duration, 6),
            "error": str(exc),
        }

    duration = time.perf_counter() - started
    observe_pipeline_stage(
        "rule_engine",
        stage_name,
        duration,
        batch_size=1,
        row_count=count,
    )
    print(f"  OK  {rule_name:<30} {count} alertas  ({duration * 1000:.1f}ms)")
    return {
        "rule_name": rule_name,
        "stage": stage_name,
        "alerts_created": count,
        "duration_seconds": round(duration, 6),
        "error": None,
    }


def run_once(cursor, window, label):
    print(f"\n{'=' * 60}")
    print(f"RULE ENGINE [{label}] -- janela: {window}")
    print(f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    cycle_started = time.perf_counter()
    rule_metrics = []
    total = 0
    for name, sql in get_rules(window):
        rule_result = execute_rule(cursor, name, sql)
        rule_metrics.append(rule_result)
        total += rule_result["alerts_created"]

    rules_duration = time.perf_counter() - cycle_started
    observe_pipeline_stage(
        "rule_engine",
        "rules_cycle",
        rules_duration,
        batch_size=len(rule_metrics),
        row_count=total,
    )

    print(f"\n  Total de alertas criados: {total}")
    print(f"{'=' * 60}\n")
    return {
        "window": window,
        "label": label,
        "alerts_created": total,
        "rules_executed": len(rule_metrics),
        "rules_duration_seconds": round(rules_duration, 6),
        "error_count": sum(1 for item in rule_metrics if item["error"]),
        "per_rule": rule_metrics,
    }


def connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "logmonitor"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme"),
    )


def _finalize_cycle(summary, commit_duration, *, configured_window=None, configured_interval_seconds=None, sleep_duration=0.0):
    active_duration = summary["rules_duration_seconds"] + commit_duration
    total_duration = active_duration + sleep_duration
    observe_pipeline_stage(
        "rule_engine",
        "commit_cycle",
        commit_duration,
        batch_size=summary["rules_executed"],
        row_count=summary["alerts_created"],
    )
    observe_pipeline_stage(
        "rule_engine",
        "cycle_total",
        active_duration,
        batch_size=summary["rules_executed"],
        row_count=summary["alerts_created"],
    )
    if sleep_duration > 0:
        observe_pipeline_stage(
            "rule_engine",
            "sleep_wait",
            sleep_duration,
            batch_size=summary["rules_executed"],
            row_count=0,
        )
    persist_component_runtime_metrics(
        "rule_engine",
        {
            **summary,
            "configured_window": configured_window,
            "configured_interval_seconds": configured_interval_seconds,
            "commit_duration_seconds": round(commit_duration, 6),
            "active_duration_seconds": round(active_duration, 6),
            "sleep_duration_seconds": round(sleep_duration, 6),
            "duration_seconds": round(total_duration, 6),
        },
    )


def mode_historical(days):
    """Corre uma vez sobre todo o historico."""
    print(f"\nModo HISTORICAL -- analisando ultimos {days} dias")
    conn = connect()
    cursor = conn.cursor()
    summary = run_once(cursor, f"{days} days", "HISTORICAL")
    commit_started = time.perf_counter()
    conn.commit()
    _finalize_cycle(summary, time.perf_counter() - commit_started, configured_window=f"{days} days")
    cursor.close()
    conn.close()
    print("Analise historica concluida.")


def mode_realtime(interval_seconds: int | None = None, window: str | None = None):
    """Corre em loop, analisando janela recente a cada interval_seconds."""
    resolved_interval = max(interval_seconds if interval_seconds is not None else _env_int("RULE_ENGINE_INTERVAL_SEC", 60), 0)
    resolved_window = window or _env_window("60 seconds")

    print(f"\nModo REALTIME -- janela: {resolved_window} | ciclo: {resolved_interval}s")
    print("Ctrl+C para parar.\n")
    conn = connect()
    cursor = conn.cursor()
    try:
        while True:
            cycle_started = time.perf_counter()
            summary = run_once(cursor, resolved_window, "REALTIME")
            commit_started = time.perf_counter()
            conn.commit()
            commit_duration = time.perf_counter() - commit_started
            active_duration = summary["rules_duration_seconds"] + commit_duration
            sleep_duration = max(resolved_interval - active_duration, 0.0)
            _finalize_cycle(
                summary,
                commit_duration,
                configured_window=resolved_window,
                configured_interval_seconds=resolved_interval,
                sleep_duration=sleep_duration,
            )
            if sleep_duration > 0:
                time.sleep(sleep_duration)
    except KeyboardInterrupt:
        print("\nParado pelo utilizador.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rule Engine -- detecao de anomalias")
    parser.add_argument(
        "--mode",
        choices=["historical", "realtime"],
        required=True,
        help="historical: analisa historico uma vez | realtime: loop continuo",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Dias a analisar no modo historical (default: 7)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Segundos entre ciclos no modo realtime (default: RULE_ENGINE_INTERVAL_SEC ou 60)",
    )
    parser.add_argument(
        "--window",
        default=None,
        help="Janela PostgreSQL interval para o modo realtime (default: RULE_ENGINE_WINDOW ou 60 seconds)",
    )
    args = parser.parse_args()

    if args.mode == "historical":
        mode_historical(args.days)
    else:
        mode_realtime(args.interval, args.window)
