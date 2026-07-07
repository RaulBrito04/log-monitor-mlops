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
from psycopg2.extras import execute_values
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
        rule_scores = self.get_rule_scores_batch([log_id])
        return rule_scores.get(log_id, (0.0, []))

    def get_rule_scores_batch(self, log_ids: list[int]) -> dict[int, tuple[float, list[str]]]:
        if not log_ids:
            return {}

        started = time.perf_counter()
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                SELECT matched.log_id, alerts.alert_type, alerts.severity
                FROM alerts
                CROSS JOIN LATERAL unnest(alerts.log_ids) AS matched(log_id)
                WHERE matched.log_id = ANY(%s)
                """,
                (log_ids,),
            )
            rows = cursor.fetchall()
        except Exception:
            observe_pipeline_stage(
                "hybrid_pipeline",
                "rule_lookup",
                time.perf_counter() - started,
                batch_size=len(log_ids),
                row_count=0,
                error_count=len(log_ids),
            )
            raise
        finally:
            cursor.close()

        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "hybrid_pipeline",
            "rule_lookup",
            duration,
            batch_size=len(log_ids),
            row_count=len(rows),
        )

        grouped: dict[int, list[tuple[str, str]]] = {}
        for matched_log_id, alert_type, severity in rows:
            grouped.setdefault(int(matched_log_id), []).append((alert_type, severity))

        severity_map = {
            "CRITICAL": 1.00,
            "HIGH": 0.75,
            "MEDIUM": 0.50,
            "LOW": 0.25,
        }
        scores_by_log: dict[int, tuple[float, list[str]]] = {}
        for log_id in log_ids:
            alerts_for_log = grouped.get(int(log_id), [])
            if not alerts_for_log:
                scores_by_log[int(log_id)] = (0.0, [])
                continue
            rule_ids = [alert_type for alert_type, _ in alerts_for_log]
            rule_score = max(severity_map.get(severity, 0.25) for _, severity in alerts_for_log)
            scores_by_log[int(log_id)] = (rule_score, rule_ids)
        return scores_by_log

    def _prepare_feature_frame(self, features_df: pd.DataFrame) -> pd.DataFrame:
        x_frame = features_df.reindex(columns=self.feature_cols, fill_value=0.0).copy()
        return x_frame.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)

    def get_ml_score(self, log_features: dict):
        started = time.perf_counter()
        try:
            row = {col: log_features.get(col, 0.0) for col in self.feature_cols}
            x_frame = self._prepare_feature_frame(pd.DataFrame([row]))
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

    def get_ml_scores_batch(self, features_df: pd.DataFrame) -> pd.DataFrame:
        if features_df.empty:
            return pd.DataFrame(columns=["ml_score", "ml_confidence"])

        started = time.perf_counter()
        try:
            x_frame = self._prepare_feature_frame(features_df)
            x_scaled = self.scaler.transform(x_frame)
            raw_scores = self.model.decision_function(x_scaled)
            raw_clamped = np.clip(raw_scores, -0.5, 0.5)
            ml_scores = 1.0 - (raw_clamped + 0.5)
            confidences = np.abs(ml_scores - 0.5) * 2.0
        except Exception:
            observe_pipeline_stage(
                "hybrid_pipeline",
                "ml_score",
                time.perf_counter() - started,
                batch_size=len(features_df),
                row_count=0,
                error_count=len(features_df),
            )
            raise

        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "hybrid_pipeline",
            "ml_score",
            duration,
            batch_size=len(features_df),
            row_count=len(features_df),
        )
        return pd.DataFrame(
            {
                "ml_score": ml_scores.astype(float),
                "ml_confidence": confidences.astype(float),
            },
            index=features_df.index,
        )

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

    def evaluate_log(self, log_id: int, log_features: dict, persist: bool = True):
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
            if persist:
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
        self._persist_batch([result])

    def _persist_batch(self, results: list[dict]):
        if not results:
            return

        started = time.perf_counter()
        cursor = self.conn.cursor()
        try:
            rows = [
                (
                    result["log_id"],
                    result["rule_score"],
                    result["ml_score"],
                    result["final_score"],
                    result["severity"],
                    result["triggered_rules"],
                    result["ml_confidence"],
                    result["is_anomaly"],
                )
                for result in results
            ]
            execute_values(
                cursor,
                """
                INSERT INTO hybrid_scores
                    (log_id, rule_score, ml_score, final_score,
                     severity, triggered_rules, ml_confidence, is_anomaly)
                VALUES %s
                ON CONFLICT DO NOTHING
                """,
                rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s)",
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            observe_pipeline_stage(
                "hybrid_pipeline",
                "persist_score",
                time.perf_counter() - started,
                batch_size=len(results),
                row_count=0,
                error_count=len(results),
            )
            raise
        finally:
            cursor.close()

        duration = time.perf_counter() - started
        observe_pipeline_stage(
            "hybrid_pipeline",
            "persist_score",
            duration,
            batch_size=len(results),
            row_count=len(results),
        )

    def evaluate_batch(self, logs_df: pd.DataFrame, verbose: bool = True):
        results = []
        total = len(logs_df)
        print(f"\nAvaliando {total:,} logs...")
        batch_started = time.perf_counter()

        log_id_series = logs_df["id"] if "id" in logs_df.columns else logs_df["log_id"]
        log_ids = log_id_series.astype(int).tolist()
        rule_scores = self.get_rule_scores_batch(log_ids)
        ml_scores_df = self.get_ml_scores_batch(logs_df)
        ml_scores = ml_scores_df["ml_score"].to_numpy()
        ml_confidences = ml_scores_df["ml_confidence"].to_numpy()

        for i, log_id in enumerate(log_ids, 1):
            rule_score, rule_ids = rule_scores.get(log_id, (0.0, []))
            ml_score = float(ml_scores[i - 1])
            ml_confidence = float(ml_confidences[i - 1])
            final_score = self.combine_scores(rule_score, ml_score)
            severity = self.classify_severity(final_score)
            results.append(
                {
                    "log_id": log_id,
                    "rule_score": rule_score,
                    "ml_score": ml_score,
                    "final_score": final_score,
                    "severity": severity,
                    "triggered_rules": rule_ids,
                    "ml_confidence": ml_confidence,
                    "is_anomaly": severity != "NORMAL",
                }
            )
            if verbose and i % 500 == 0:
                print(f"  {i:,}/{total:,} avaliados...")

        self._persist_batch(results)
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
