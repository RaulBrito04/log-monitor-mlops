#!/usr/bin/env python3
"""
Hybrid Detection Pipeline - Semana 7
Combina Rule Engine (Semana 4) com Isolation Forest (Semana 6)
"""

from __future__ import annotations

import os
import pickle  # nosec B403
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.monitoring.metrics import observe_pipeline_stage, persist_component_runtime_metrics

load_dotenv()

RULE_WEIGHT = 0.55
ML_WEIGHT = 0.45

THRESHOLDS = {
    "CRITICAL": 0.80,
    "HIGH": 0.60,
    "MEDIUM": 0.40,
}


def _safe_load_pickle(path: str, allowed_dir: str = "models"):
    allowed_root = Path(allowed_dir).resolve()
    resolved = Path(path).resolve()

    if allowed_root not in resolved.parents and resolved != allowed_root:
        raise ValueError(
            f"Artefacto fora do diretorio permitido: {path}. "
            f"Apenas artefactos dentro de '{allowed_dir}' podem ser carregados."
        )

    if resolved.suffix != ".pkl":
        raise ValueError(
            f"Extensao invalida '{resolved.suffix}'. Apenas .pkl e permitido."
        )

    if not resolved.exists():
        raise FileNotFoundError(f"Artefacto nao encontrado: {resolved}")

    with open(resolved, "rb") as handle:
        return pickle.load(handle)  # nosec B301


class HybridPipeline:
    def __init__(
        self,
        model_path="models/isolation_forest_latest.pkl",
        scaler_path="models/scaler.pkl",
        features_path="data/selected_features.txt",
        rule_weight=RULE_WEIGHT,
        ml_weight=ML_WEIGHT,
    ):
        if abs(rule_weight + ml_weight - 1.0) >= 1e-6:
            raise ValueError("rule_weight + ml_weight deve ser 1.0")
        self.rule_weight = rule_weight
        self.ml_weight = ml_weight

        bundle = _safe_load_pickle(model_path, allowed_dir="models")
        self.model = bundle["model"]
        self.scaler = bundle["scaler"]
        self.scaler_path = scaler_path
        self.last_batch_summary: dict[str, object] | None = None

        with open(features_path, encoding="utf-8") as handle:
            self.feature_cols = [line.strip() for line in handle if line.strip()]

        self.conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", 5432)),
            database=os.getenv("POSTGRES_DB", "logmonitor"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "changeme"),
        )
        self.conn.autocommit = False

        print("HybridPipeline iniciado")
        print(f"  rule_weight={self.rule_weight}, ml_weight={self.ml_weight}")
        print(f"  Features esperadas: {len(self.feature_cols)}")

    def get_rule_score(self, log_id: int):
        started = time.perf_counter()
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                SELECT alert_type, severity
                FROM alerts
                WHERE %s = ANY(log_ids)
                ORDER BY severity DESC
                """,
                (log_id,),
            )
            rows = cursor.fetchall()
        except Exception:
            observe_pipeline_stage(
                "hybrid_pipeline",
                "rule_lookup",
                time.perf_counter() - started,
                batch_size=1,
                row_count=0,
                error_count=1,
            )
            raise
        finally:
            cursor.close()

        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "hybrid_pipeline",
            "rule_lookup",
            duration,
            batch_size=1,
            row_count=len(rows),
        )

        if not rows:
            return 0.0, []

        severity_map = {
            "CRITICAL": 1.00,
            "HIGH": 0.75,
            "MEDIUM": 0.50,
            "LOW": 0.25,
        }
        scores = [severity_map.get(row[1], 0.25) for row in rows]
        rule_score = max(scores)
        rule_ids = [row[0] for row in rows]
        return rule_score, rule_ids

    def get_ml_score(self, log_features: dict):
        started = time.perf_counter()
        try:
            row = {col: log_features.get(col, 0.0) for col in self.feature_cols}
            x_frame = pd.DataFrame([row])
            x_frame = x_frame.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
            x_scaled = self.scaler.transform(x_frame)
            raw_score = self.model.decision_function(x_scaled)[0]
            raw_clamped = np.clip(raw_score, -0.5, 0.5)
            ml_score = 1.0 - (raw_clamped + 0.5)
            confidence = abs(ml_score - 0.5) * 2.0
        except Exception:
            observe_pipeline_stage(
                "hybrid_pipeline",
                "ml_score",
                time.perf_counter() - started,
                batch_size=1,
                row_count=0,
                error_count=1,
            )
            raise

        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "hybrid_pipeline",
            "ml_score",
            duration,
            batch_size=1,
            row_count=1,
        )
        return float(ml_score), float(confidence)

    def combine_scores(self, rule_score: float, ml_score: float):
        final = rule_score * self.rule_weight + ml_score * self.ml_weight
        if rule_score >= 1.0:
            final = max(final, 0.75)
        return round(float(final), 4)

    def classify_severity(self, final_score: float):
        if final_score >= THRESHOLDS["CRITICAL"]:
            return "CRITICAL"
        if final_score >= THRESHOLDS["HIGH"]:
            return "HIGH"
        if final_score >= THRESHOLDS["MEDIUM"]:
            return "MEDIUM"
        return "NORMAL"

    def evaluate_log(self, log_id: int, log_features: dict):
        started = time.perf_counter()
        try:
            rule_score, rule_ids = self.get_rule_score(log_id)
            ml_score, ml_confidence = self.get_ml_score(log_features)
            final_score = self.combine_scores(rule_score, ml_score)
            severity = self.classify_severity(final_score)

            result = {
                "log_id": log_id,
                "rule_score": rule_score,
                "ml_score": ml_score,
                "final_score": final_score,
                "severity": severity,
                "triggered_rules": rule_ids,
                "ml_confidence": ml_confidence,
                "is_anomaly": severity != "NORMAL",
            }
            self._persist(result)
        except Exception:
            observe_pipeline_stage(
                "hybrid_pipeline",
                "evaluate_log",
                time.perf_counter() - started,
                batch_size=1,
                row_count=0,
                error_count=1,
            )
            raise

        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "hybrid_pipeline",
            "evaluate_log",
            duration,
            batch_size=1,
            row_count=1,
        )
        return result

    def _persist(self, result: dict):
        started = time.perf_counter()
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO hybrid_scores
                    (log_id, rule_score, ml_score, final_score,
                     severity, triggered_rules, ml_confidence, is_anomaly)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    result["log_id"],
                    result["rule_score"],
                    result["ml_score"],
                    result["final_score"],
                    result["severity"],
                    result["triggered_rules"],
                    result["ml_confidence"],
                    result["is_anomaly"],
                ),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            observe_pipeline_stage(
                "hybrid_pipeline",
                "persist_score",
                time.perf_counter() - started,
                batch_size=1,
                row_count=0,
                error_count=1,
            )
            raise
        finally:
            cursor.close()

        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "hybrid_pipeline",
            "persist_score",
            duration,
            batch_size=1,
            row_count=1,
        )

    def evaluate_batch(self, logs_df: pd.DataFrame, verbose: bool = True):
        results = []
        total = len(logs_df)
        print(f"\nAvaliando {total:,} logs...")
        batch_started = time.perf_counter()

        for i, (_, row) in enumerate(logs_df.iterrows(), 1):
            log_id = int(row["id"]) if "id" in row else int(row["log_id"])
            features = row.to_dict()
            result = self.evaluate_log(log_id, features)
            results.append(result)
            if verbose and i % 500 == 0:
                print(f"  {i:,}/{total:,} avaliados...")

        results_df = pd.DataFrame(results)
        duration = time.perf_counter() - batch_started
        throughput = total / duration if duration > 0 else 0.0
        anomalies_found = int(results_df["is_anomaly"].sum()) if not results_df.empty else 0
        observe_pipeline_stage(
            "hybrid_pipeline",
            "evaluate_batch",
            duration,
            batch_size=total,
            row_count=anomalies_found,
        )
        self.last_batch_summary = {
            "logs_processed": total,
            "anomalies_found": anomalies_found,
            "duration_seconds": round(duration, 6),
            "throughput_logs_per_second": round(throughput, 6),
        }
        persist_component_runtime_metrics(
            "hybrid_pipeline",
            {
                "logs_processed": total,
                "anomalies_found": anomalies_found,
                "duration_seconds": round(duration, 6),
                "throughput_logs_per_second": round(throughput, 6),
                "average_final_score": round(float(results_df["final_score"].mean()), 6) if not results_df.empty else 0.0,
                "average_rule_score": round(float(results_df["rule_score"].mean()), 6) if not results_df.empty else 0.0,
                "average_ml_score": round(float(results_df["ml_score"].mean()), 6) if not results_df.empty else 0.0,
            },
        )
        if verbose:
            print("\nBatch completo!")
            self._print_summary(results_df)
        return results_df

    def _print_summary(self, results_df: pd.DataFrame):
        print("\n" + "=" * 60)
        print("SUMARIO DO PIPELINE HIBRIDO")
        print("=" * 60)

        total = len(results_df)
        if results_df.empty:
            print("DataFrame vazio, nada para sumarizar.")
            return

        severity_counts = results_df["severity"].value_counts()
        for sev in ["CRITICAL", "HIGH", "MEDIUM", "NORMAL"]:
            count = severity_counts.get(sev, 0)
            pct = 100 * count / total
            bar = "#" * int(pct / 2)
            print(f"  {sev:<10} {count:>5} ({pct:5.1f}%)  {bar}")

        print(f"\n  Total avaliado : {total:,}")
        print(f"  Anomalias      : {results_df['is_anomaly'].sum():,} ({100 * results_df['is_anomaly'].mean():.1f}%)")
        print(f"  Score medio    : {results_df['final_score'].mean():.3f}")
        print(f"  Rule score medio: {results_df['rule_score'].mean():.3f}")
        print(f"  ML score medio  : {results_df['ml_score'].mean():.3f}")
        print("=" * 60)
