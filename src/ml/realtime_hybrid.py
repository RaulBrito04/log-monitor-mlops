from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from .feature_engineering import FeatureEngineer
    from .hybrid_pipeline import HybridPipeline
except ImportError:
    from feature_engineering import FeatureEngineer
    from hybrid_pipeline import HybridPipeline

from src.monitoring.metrics import observe_pipeline_stage, persist_component_runtime_metrics

load_dotenv()


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


class RealtimeHybridProcessor:
    def __init__(self, poll_interval_sec: float | None = None, fetch_limit: int | None = None):
        resolved_poll_interval = poll_interval_sec if poll_interval_sec is not None else _env_float("HYBRID_POLL_INTERVAL_SEC", 30.0)
        resolved_fetch_limit = fetch_limit if fetch_limit is not None else _env_int("HYBRID_FETCH_LIMIT", 500)

        self.poll_interval = max(float(resolved_poll_interval), 0.0)
        self.fetch_limit = max(int(resolved_fetch_limit), 1)
        self.pipeline = HybridPipeline()
        self.feat_eng = FeatureEngineer()
        self.fetch_conn = None
        self.last_processed_id = self._get_last_processed_id()
        print("RealtimeHybridProcessor iniciado")
        print(f"  Ultimo log processado: id={self.last_processed_id}")
        print(f"  Poll interval: {self.poll_interval}s")
        print(f"  Fetch limit: {self.fetch_limit}")
        if self.poll_interval > 0:
            print(f"  Teto configurado: {self.fetch_limit / self.poll_interval:.3f} logs/s")
        else:
            print("  Teto configurado: sem espera artificial entre ciclos")

    def _db_config(self) -> dict[str, object]:
        return {
            "host": os.getenv("POSTGRES_HOST", "localhost"),
            "port": int(os.getenv("POSTGRES_PORT", "5432")),
            "database": os.getenv("POSTGRES_DB", "logmonitor"),
            "user": os.getenv("POSTGRES_USER", "postgres"),
            "password": os.getenv("POSTGRES_PASSWORD", "changeme"),
        }

    def _get_fetch_connection(self):
        if self.fetch_conn is None or getattr(self.fetch_conn, "closed", 1):
            self.fetch_conn = psycopg2.connect(**self._db_config())
        return self.fetch_conn

    def close(self):
        if self.fetch_conn is not None and not getattr(self.fetch_conn, "closed", 1):
            self.fetch_conn.close()
        self.fetch_conn = None

    def _get_last_processed_id(self):
        """Encontra o ultimo log_id ja avaliado."""
        conn = psycopg2.connect(**self._db_config())
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(log_id), 0) FROM hybrid_scores")
        last_id = cur.fetchone()[0]
        cur.close()
        conn.close()
        return last_id

    def configured_ceiling_logs_per_second(self) -> float | None:
        if self.poll_interval <= 0:
            return None
        return self.fetch_limit / self.poll_interval

    def fetch_new_logs(self):
        """Busca logs novos que ainda nao foram avaliados."""
        started = time.perf_counter()
        query = """
            SELECT id, timestamp, ip, method, endpoint,
                   status, response_time_ms, user_agent
            FROM   raw_logs
            WHERE  id > %s
            ORDER  BY id
            LIMIT  %s
        """
        columns = ["id", "timestamp", "ip", "method", "endpoint", "status", "response_time_ms", "user_agent"]
        cursor = None
        try:
            cursor = self._get_fetch_connection().cursor()
            cursor.execute(query, (self.last_processed_id, self.fetch_limit))
            rows = cursor.fetchall()
            df = pd.DataFrame.from_records(rows, columns=columns)
        except Exception:
            self.close()
            observe_pipeline_stage(
                "realtime_hybrid",
                "fetch_new_logs",
                time.perf_counter() - started,
                batch_size=self.fetch_limit,
                row_count=0,
                error_count=1,
            )
            raise
        finally:
            if cursor is not None:
                cursor.close()

        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "realtime_hybrid",
            "fetch_new_logs",
            duration,
            batch_size=self.fetch_limit,
            row_count=len(df),
        )
        return df, duration

    def process_logs(self, raw_df):
        """Extrai features e avalia cada log."""
        if raw_df.empty:
            return {
                "logs_processed": 0,
                "anomalies_found": 0,
                "feature_duration_seconds": 0.0,
                "evaluation_duration_seconds": 0.0,
                "duration_seconds": 0.0,
            }

        started = time.perf_counter()
        feature_started = time.perf_counter()
        features_df, _ = self.feat_eng.extract_features(raw_df)
        features_df["log_id"] = raw_df["id"].values
        feature_duration = time.perf_counter() - feature_started
        observe_pipeline_stage(
            "realtime_hybrid",
            "feature_engineering",
            feature_duration,
            batch_size=len(raw_df),
            row_count=len(features_df),
        )

        evaluation_started = time.perf_counter()
        results_df = self.pipeline.evaluate_batch(features_df, verbose=False)
        anomalies_df = results_df[results_df["is_anomaly"]]
        anomalies_found = len(anomalies_df)
        for _, result in anomalies_df.iterrows():
            sev = result["severity"]
            score = result["final_score"]
            rules = ", ".join(result["triggered_rules"]) or "none"
            print(f"  [{sev}] log_id={int(result['log_id'])} score={score:.3f} rules=[{rules}]")
        evaluation_duration = time.perf_counter() - evaluation_started
        observe_pipeline_stage(
            "realtime_hybrid",
            "evaluate_logs",
            evaluation_duration,
            batch_size=len(features_df),
            row_count=anomalies_found,
        )

        self.last_processed_id = int(features_df["log_id"].max())
        process_duration = time.perf_counter() - started

        n = len(features_df)
        print(f"  Processados: {n} logs | Anomalias: {anomalies_found} ({100 * anomalies_found / n:.1f}%)")
        return {
            "logs_processed": n,
            "anomalies_found": anomalies_found,
            "feature_duration_seconds": round(feature_duration, 6),
            "evaluation_duration_seconds": round(evaluation_duration, 6),
            "duration_seconds": round(process_duration, 6),
        }

    def _sleep_until_next_cycle(self, cycle_duration: float) -> float:
        remaining_sleep = max(self.poll_interval - cycle_duration, 0.0)
        if remaining_sleep > 0:
            observe_pipeline_stage(
                "realtime_hybrid",
                "sleep_wait",
                remaining_sleep,
                batch_size=self.fetch_limit,
                row_count=0,
            )
            time.sleep(remaining_sleep)
        return remaining_sleep

    def run_cycle(self, *, sleep_after_cycle: bool = True) -> dict[str, float | int | bool]:
        cycle_started = time.perf_counter()
        ts = datetime.now().strftime("%H:%M:%S")
        new_logs, fetch_duration = self.fetch_new_logs()

        if new_logs.empty:
            active_duration = time.perf_counter() - cycle_started
            observe_pipeline_stage(
                "realtime_hybrid",
                "cycle_total",
                active_duration,
                batch_size=0,
                row_count=0,
            )
            sleep_duration = self._sleep_until_next_cycle(active_duration) if sleep_after_cycle else 0.0
            print(f"[{ts}] Sem logs novos. A aguardar {max(self.poll_interval - active_duration, 0.0):.2f}s...")
            return {
                "had_work": False,
                "logs_processed": 0,
                "anomalies_found": 0,
                "fetch_duration_seconds": round(fetch_duration, 6),
                "feature_duration_seconds": 0.0,
                "evaluation_duration_seconds": 0.0,
                "active_duration_seconds": round(active_duration, 6),
                "sleep_duration_seconds": round(sleep_duration, 6),
                "duration_seconds": round(active_duration + sleep_duration, 6),
                "throughput_logs_per_second": 0.0,
            }

        print(f"[{ts}] {len(new_logs)} logs novos encontrados:")
        processing_summary = self.process_logs(new_logs)
        active_duration = time.perf_counter() - cycle_started
        sleep_duration = self._sleep_until_next_cycle(active_duration) if sleep_after_cycle else 0.0
        loop_duration = active_duration + sleep_duration
        throughput = processing_summary["logs_processed"] / loop_duration if loop_duration > 0 else 0.0
        configured_ceiling = self.configured_ceiling_logs_per_second()

        observe_pipeline_stage(
            "realtime_hybrid",
            "cycle_total",
            active_duration,
            batch_size=processing_summary["logs_processed"],
            row_count=processing_summary["anomalies_found"],
        )
        snapshot = {
            "logs_processed": processing_summary["logs_processed"],
            "anomalies_found": processing_summary["anomalies_found"],
            "fetch_duration_seconds": round(fetch_duration, 6),
            "feature_duration_seconds": processing_summary["feature_duration_seconds"],
            "evaluation_duration_seconds": processing_summary["evaluation_duration_seconds"],
            "active_duration_seconds": round(active_duration, 6),
            "sleep_duration_seconds": round(sleep_duration, 6),
            "duration_seconds": round(loop_duration, 6),
            "throughput_logs_per_second": round(throughput, 6),
            "last_processed_id": self.last_processed_id,
            "configured_fetch_limit": self.fetch_limit,
            "configured_poll_interval_seconds": round(self.poll_interval, 3),
            "configured_ceiling_logs_per_second": round(configured_ceiling, 6) if configured_ceiling is not None else None,
        }
        persist_component_runtime_metrics("realtime_hybrid", snapshot)
        return {"had_work": True, **snapshot}

    def run(self):
        """Loop principal."""
        print(f"\n{'=' * 60}")
        print("REALTIME HYBRID PROCESSOR")
        print(f"{'=' * 60}")
        print("A iniciar loop (Ctrl+C para parar)...\n")

        while True:
            self.run_cycle(sleep_after_cycle=True)


if __name__ == "__main__":
    processor = RealtimeHybridProcessor()
    processor.run()
