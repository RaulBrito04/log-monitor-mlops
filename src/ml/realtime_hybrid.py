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


class RealtimeHybridProcessor:
    def __init__(self, poll_interval_sec=30):
        self.poll_interval = poll_interval_sec
        self.pipeline = HybridPipeline()
        self.feat_eng = FeatureEngineer()
        self.last_processed_id = self._get_last_processed_id()
        print("✓ RealtimeHybridProcessor iniciado")
        print(f"  Último log processado: id={self.last_processed_id}")
        print(f"  Poll interval: {self.poll_interval}s")

    def _get_last_processed_id(self):
        """Encontra o último log_id já avaliado."""
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            database=os.getenv("POSTGRES_DB", "logmonitor"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "changeme"),
        )
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(log_id), 0) FROM hybrid_scores")
        last_id = cur.fetchone()[0]
        cur.close()
        conn.close()
        return last_id

    def fetch_new_logs(self):
        """Busca logs novos que ainda não foram avaliados."""
        started = time.perf_counter()
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            database=os.getenv("POSTGRES_DB", "logmonitor"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "changeme"),
        )
        query = """
            SELECT id, timestamp, ip, method, endpoint,
                   status, response_time_ms, user_agent
            FROM   raw_logs
            WHERE  id > %s
            ORDER  BY id
            LIMIT  500
        """
        try:
            df = pd.read_sql(query, conn, params=(self.last_processed_id,))
        except Exception:
            observe_pipeline_stage(
                "realtime_hybrid",
                "fetch_new_logs",
                time.perf_counter() - started,
                batch_size=1,
                row_count=0,
                error_count=1,
            )
            conn.close()
            raise

        conn.close()
        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "realtime_hybrid",
            "fetch_new_logs",
            duration,
            batch_size=1,
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
        anomalies_found = 0
        for _, row in features_df.iterrows():
            result = self.pipeline.evaluate_log(
                log_id=int(row["log_id"]),
                log_features=row.to_dict(),
            )
            if result["is_anomaly"]:
                anomalies_found += 1
                sev = result["severity"]
                score = result["final_score"]
                rules = ", ".join(result["triggered_rules"]) or "none"
                print(f"  [{sev}] log_id={result['log_id']} score={score:.3f} rules=[{rules}]")
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

    def run(self):
        """Loop principal."""
        print(f"\n{'=' * 60}")
        print("REALTIME HYBRID PROCESSOR")
        print(f"{'=' * 60}")
        print("A iniciar loop (Ctrl+C para parar)...\n")

        while True:
            cycle_started = time.perf_counter()
            ts = datetime.now().strftime("%H:%M:%S")
            new_logs, fetch_duration = self.fetch_new_logs()

            if new_logs.empty:
                cycle_duration = time.perf_counter() - cycle_started
                observe_pipeline_stage(
                    "realtime_hybrid",
                    "cycle_total",
                    cycle_duration,
                    batch_size=0,
                    row_count=0,
                )
                print(f"[{ts}] Sem logs novos. A aguardar {self.poll_interval}s...")
            else:
                print(f"[{ts}] {len(new_logs)} logs novos encontrados:")
                processing_summary = self.process_logs(new_logs)
                cycle_duration = time.perf_counter() - cycle_started
                throughput = processing_summary["logs_processed"] / cycle_duration if cycle_duration > 0 else 0.0
                observe_pipeline_stage(
                    "realtime_hybrid",
                    "cycle_total",
                    cycle_duration,
                    batch_size=processing_summary["logs_processed"],
                    row_count=processing_summary["anomalies_found"],
                )
                persist_component_runtime_metrics(
                    "realtime_hybrid",
                    {
                        "logs_processed": processing_summary["logs_processed"],
                        "anomalies_found": processing_summary["anomalies_found"],
                        "fetch_duration_seconds": round(fetch_duration, 6),
                        "feature_duration_seconds": processing_summary["feature_duration_seconds"],
                        "evaluation_duration_seconds": processing_summary["evaluation_duration_seconds"],
                        "duration_seconds": round(cycle_duration, 6),
                        "throughput_logs_per_second": round(throughput, 6),
                        "last_processed_id": self.last_processed_id,
                    },
                )

            time.sleep(self.poll_interval)


if __name__ == "__main__":
    processor = RealtimeHybridProcessor(poll_interval_sec=30)
    processor.run()
