#!/usr/bin/env python3
"""Feedback-driven reviewed dataset builder and safe candidate retraining."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import math
import os
import pickle
from typing import Any

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import psycopg2
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

from src.ml.feature_engineering import FeatureEngineer
from src.monitoring.metrics import persist_runtime_metrics, refresh_monitoring_metrics

DEFAULT_MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
DEFAULT_EXPERIMENT_NAME = "feedback-retraining-experiments"
SUPPORTED_TRAINING_LABELS = {"true_positive": 1, "false_positive": 0}
RUNTIME_NOTE = (
    "Current realtime pipeline uses rules plus IsolationForest. "
    "This reviewed-feedback retraining stage evaluates an offline RandomForest candidate only."
)


@dataclass(frozen=True)
class SplitConfig:
    reviewed_holdout_frac: float = 0.25
    temporal_holdout_frac: float = 0.20
    min_reviewed_holdout_rows: int = 10
    min_temporal_holdout_rows: int = 10


@dataclass(frozen=True)
class PromotionConfig:
    min_feedback_events: int = 25
    min_log_samples: int = 50
    min_new_feedback_events: int = 10
    min_train_rows: int = 30
    min_positive_train_rows: int = 5
    min_positive_holdout_rows: int = 3
    min_negative_holdout_rows: int = 3
    min_reviewed_f1_gain: float = 0.02
    max_temporal_f1_regression: float = 0.01
    max_temporal_precision_drop: float = 0.02


@dataclass(frozen=True)
class CandidateConfig:
    n_estimators: int
    class_weight: str | None
    thresholds: tuple[float, ...]


def load_pickle_with_csv_fallback(
    pickle_path: Path,
    csv_path: Path,
    parse_dates: list[str] | None = None,
) -> pd.DataFrame:
    try:
        return pd.read_pickle(pickle_path)
    except (FileNotFoundError, NotImplementedError, ModuleNotFoundError, AttributeError):
        if not csv_path.exists():
            raise
        return pd.read_csv(csv_path, parse_dates=parse_dates or [])


def parse_log_ids(value: Any) -> list[int]:
    if isinstance(value, np.ndarray):
        return [int(item) for item in value.tolist()]
    if isinstance(value, (list, tuple, set)):
        return [int(item) for item in value]
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    if isinstance(value, str):
        stripped = value.strip().strip("{}[]")
        if not stripped:
            return []
        return [int(part.strip()) for part in stripped.split(",") if part.strip()]
    return [int(value)]


def map_feedback_label_to_training_target(label: str) -> tuple[int | None, bool, str | None]:
    if label in SUPPORTED_TRAINING_LABELS:
        return SUPPORTED_TRAINING_LABELS[label], True, None
    if label == "false_negative":
        return None, False, "false_negative_requires_missed_event_linkage"
    return None, False, "unsupported_feedback_label"


def deduplicate_latest_feedback(feedback_events: pd.DataFrame) -> pd.DataFrame:
    if feedback_events.empty:
        return feedback_events.copy()
    ordered = feedback_events.sort_values(["alert_id", "feedback_created_at", "feedback_id"], ascending=[True, False, False])
    return ordered.drop_duplicates(subset=["alert_id"], keep="first").reset_index(drop=True)


def explode_feedback_events(feedback_events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if feedback_events.empty:
        return pd.DataFrame()

    for record in feedback_events.to_dict(orient="records"):
        log_ids = parse_log_ids(record.get("log_ids"))
        if not log_ids:
            rows.append({**record, "log_id": np.nan, "log_position": -1})
            continue
        for position, log_id in enumerate(log_ids):
            rows.append({**record, "log_id": int(log_id), "log_position": position})

    exploded = pd.DataFrame(rows)
    if exploded.empty:
        return exploded

    conflict_map = (
        exploded.dropna(subset=["log_id"])
        .groupby("log_id")["feedback_label"]
        .nunique()
        .gt(1)
        .to_dict()
    )
    exploded["had_feedback_conflict"] = exploded["log_id"].map(conflict_map).fillna(False)

    dedupable = exploded.dropna(subset=["log_id"]).copy()
    dedupable = dedupable.sort_values(["log_id", "feedback_created_at", "feedback_id"], ascending=[True, False, False])
    deduped_logs = dedupable.drop_duplicates(subset=["log_id"], keep="first")

    without_logs = exploded[exploded["log_id"].isna()].copy()
    combined = pd.concat([deduped_logs, without_logs], ignore_index=True, sort=False)
    return combined.sort_values(["feedback_created_at", "feedback_id"], ascending=[False, False]).reset_index(drop=True)

def build_reviewed_splits(
    dataset: pd.DataFrame,
    split_config: SplitConfig = SplitConfig(),
    label_col: str = "reviewed_label",
    timestamp_col: str = "timestamp",
) -> dict[str, pd.DataFrame]:
    if dataset.empty:
        raise ValueError("Reviewed dataset is empty")

    ordered = dataset.sort_values(timestamp_col).reset_index(drop=True)
    temporal_size = max(split_config.min_temporal_holdout_rows, int(math.ceil(len(ordered) * split_config.temporal_holdout_frac)))
    if temporal_size >= len(ordered):
        raise ValueError("Not enough rows to create a temporal holdout")

    temporal_holdout = ordered.tail(temporal_size).reset_index(drop=True)
    remaining = ordered.iloc[:-temporal_size].reset_index(drop=True)

    reviewed_size = max(split_config.min_reviewed_holdout_rows, int(math.ceil(len(remaining) * split_config.reviewed_holdout_frac)))
    if reviewed_size >= len(remaining):
        reviewed_size = max(1, len(remaining) // 3)
    if reviewed_size <= 0 or reviewed_size >= len(remaining):
        raise ValueError("Not enough rows to create a reviewed holdout")

    stratify = None
    label_counts = remaining[label_col].value_counts()
    if remaining[label_col].nunique() > 1 and int(label_counts.min()) >= 2:
        stratify = remaining[label_col]

    train_df, reviewed_holdout = train_test_split(
        remaining,
        test_size=reviewed_size,
        random_state=42,
        shuffle=True,
        stratify=stratify,
    )
    return {
        "train": train_df.sort_values(timestamp_col).reset_index(drop=True),
        "reviewed_holdout": reviewed_holdout.sort_values(timestamp_col).reset_index(drop=True),
        "temporal_holdout": temporal_holdout,
    }


def evaluate_binary_scores(scores: np.ndarray, y_true: pd.Series | np.ndarray, threshold: float) -> tuple[dict[str, float], np.ndarray]:
    y_true_array = np.asarray(y_true, dtype=int)
    y_pred = (np.asarray(scores, dtype=float) >= threshold).astype(int)
    precision = precision_score(y_true_array, y_pred, zero_division=0)
    recall = recall_score(y_true_array, y_pred, zero_division=0)
    f1_value = f1_score(y_true_array, y_pred, zero_division=0)
    try:
        roc_auc = roc_auc_score(y_true_array, np.asarray(scores, dtype=float))
    except Exception:
        roc_auc = 0.5
    metrics = {
        "precision": float(precision),
        "recall": float(recall),
        "f1_score": float(f1_value),
        "roc_auc": float(roc_auc),
        "accuracy": float((y_pred == y_true_array).mean()),
        "positives": int(y_true_array.sum()),
        "negatives": int((y_true_array == 0).sum()),
        "predicted_positives": int(y_pred.sum()),
        "threshold": float(threshold),
    }
    return metrics, y_pred


def find_best_threshold(scores: np.ndarray, y_true: pd.Series | np.ndarray, thresholds: list[float] | tuple[float, ...]) -> dict[str, float]:
    best: dict[str, float] | None = None
    for threshold in thresholds:
        metrics, _ = evaluate_binary_scores(scores, y_true, float(threshold))
        candidate_key = (metrics["f1_score"], metrics["precision"], metrics["recall"], -float(threshold))
        if best is None or candidate_key > best["key"]:
            best = {**metrics, "key": candidate_key}
    if best is None:
        raise ValueError("Threshold search received no candidate thresholds")
    return best


def summarize_split(frame: pd.DataFrame, label_col: str = "reviewed_label") -> dict[str, int]:
    labels = frame[label_col].astype(int) if not frame.empty else pd.Series(dtype=int)
    return {
        "rows": int(len(frame)),
        "positives": int(labels.sum()) if not labels.empty else 0,
        "negatives": int((labels == 0).sum()) if not labels.empty else 0,
        "feedback_events": int(frame["feedback_id"].nunique()) if "feedback_id" in frame.columns else 0,
    }


def build_promotion_decision(
    dataset_summary: dict[str, Any],
    split_summary: dict[str, dict[str, int]],
    baseline_metrics: dict[str, dict[str, float]] | None,
    candidate_metrics: dict[str, dict[str, float]],
    promotion_config: PromotionConfig,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    checks["baseline_available"] = baseline_metrics is not None
    checks["enough_feedback_events"] = int(dataset_summary.get("eligible_feedback_events", 0)) >= promotion_config.min_feedback_events
    checks["enough_log_samples"] = int(dataset_summary.get("ready_for_training_log_samples", 0)) >= promotion_config.min_log_samples
    checks["enough_new_feedback_events"] = int(dataset_summary.get("new_feedback_events_since_current_model", 0)) >= promotion_config.min_new_feedback_events
    checks["enough_train_rows"] = int(split_summary["train"]["rows"]) >= promotion_config.min_train_rows
    checks["enough_positive_train_rows"] = int(split_summary["train"]["positives"]) >= promotion_config.min_positive_train_rows
    checks["enough_positive_reviewed_holdout"] = int(split_summary["reviewed_holdout"]["positives"]) >= promotion_config.min_positive_holdout_rows
    checks["enough_negative_reviewed_holdout"] = int(split_summary["reviewed_holdout"]["negatives"]) >= promotion_config.min_negative_holdout_rows
    checks["enough_positive_temporal_holdout"] = int(split_summary["temporal_holdout"]["positives"]) >= promotion_config.min_positive_holdout_rows
    checks["enough_negative_temporal_holdout"] = int(split_summary["temporal_holdout"]["negatives"]) >= promotion_config.min_negative_holdout_rows

    if baseline_metrics is not None:
        checks["reviewed_f1_improved"] = (
            candidate_metrics["reviewed_holdout"]["f1_score"]
            >= baseline_metrics["reviewed_holdout"]["f1_score"] + promotion_config.min_reviewed_f1_gain
        )
        checks["temporal_f1_not_regressed"] = (
            candidate_metrics["temporal_holdout"]["f1_score"]
            >= baseline_metrics["temporal_holdout"]["f1_score"] - promotion_config.max_temporal_f1_regression
        )
        checks["temporal_precision_not_regressed"] = (
            candidate_metrics["temporal_holdout"]["precision"]
            >= baseline_metrics["temporal_holdout"]["precision"] - promotion_config.max_temporal_precision_drop
        )
    else:
        checks["reviewed_f1_improved"] = False
        checks["temporal_f1_not_regressed"] = False
        checks["temporal_precision_not_regressed"] = False

    for check_name, passed in checks.items():
        if not passed:
            reasons.append(check_name)

    return {
        "promotable": all(checks.values()),
        "checks": checks,
        "failed_checks": reasons,
        "config": asdict(promotion_config),
    }


def build_retraining_runtime_metrics_payload(
    report: dict[str, Any],
    dataset_summary: dict[str, Any],
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    promotion: dict[str, Any] | None,
) -> dict[str, Any]:
    def _metric(bundle: dict[str, Any] | None, split_name: str, metric_name: str) -> float:
        if not bundle:
            return 0.0
        split_metrics = bundle.get(split_name) or {}
        return float(split_metrics.get(metric_name, 0.0) or 0.0)

    reviewed_baseline_f1 = _metric(baseline, "reviewed_holdout", "f1_score")
    reviewed_candidate_f1 = _metric(candidate, "reviewed_holdout", "f1_score")
    temporal_baseline_f1 = _metric(baseline, "temporal_holdout", "f1_score")
    temporal_candidate_f1 = _metric(candidate, "temporal_holdout", "f1_score")
    temporal_baseline_precision = _metric(baseline, "temporal_holdout", "precision")
    temporal_candidate_precision = _metric(candidate, "temporal_holdout", "precision")

    deltas_available = baseline is not None and candidate is not None
    return {
        "retraining_status": str(report.get("status", "unknown")),
        "retraining_updated_at": str(report.get("generated_at") or datetime.now(timezone.utc).isoformat()),
        "retraining_baseline_available": int(baseline is not None),
        "retraining_candidate_available": int(candidate is not None),
        "retraining_promotable": int(bool(promotion and promotion.get("promotable"))),
        "retraining_feedback_events": int(dataset_summary.get("eligible_feedback_events", 0) or 0),
        "retraining_ready_log_samples": int(dataset_summary.get("ready_for_training_log_samples", 0) or 0),
        "retraining_new_feedback_events_since_current_model": int(
            dataset_summary.get("new_feedback_events_since_current_model", 0) or 0
        ),
        "retraining_new_training_rows_since_current_model": int(
            dataset_summary.get("new_training_rows_since_current_model", 0) or 0
        ),
        "retraining_reviewed_holdout_baseline_f1_score": reviewed_baseline_f1,
        "retraining_reviewed_holdout_candidate_f1_score": reviewed_candidate_f1,
        "retraining_temporal_holdout_baseline_f1_score": temporal_baseline_f1,
        "retraining_temporal_holdout_candidate_f1_score": temporal_candidate_f1,
        "retraining_temporal_holdout_baseline_precision": temporal_baseline_precision,
        "retraining_temporal_holdout_candidate_precision": temporal_candidate_precision,
        "retraining_reviewed_f1_delta": reviewed_candidate_f1 - reviewed_baseline_f1 if deltas_available else 0.0,
        "retraining_temporal_f1_delta": temporal_candidate_f1 - temporal_baseline_f1 if deltas_available else 0.0,
        "retraining_temporal_precision_delta": (
            temporal_candidate_precision - temporal_baseline_precision if deltas_available else 0.0
        ),
    }


class ReviewedFeedbackBuilder:
    """Build reviewed datasets from feedback, alerts, and feature artifacts."""

    def __init__(self, data_dir: str = "data", models_dir: str = "models"):
        self.data_dir = Path(data_dir)
        self.models_dir = Path(models_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.reviewed_dataset_path = self.data_dir / "reviewed_feedback_dataset.pkl"
        self.reviewed_dataset_csv_path = self.data_dir / "reviewed_feedback_dataset.csv"
        self.reviewed_iforest_path = self.data_dir / "reviewed_feedback_dataset_iforest.pkl"
        self.reviewed_iforest_csv_path = self.data_dir / "reviewed_feedback_dataset_iforest.csv"
        self.summary_path = self.data_dir / "reviewed_feedback_summary.json"

    def _connect_db(self):
        return psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=os.getenv("POSTGRES_DB", "logmonitor"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "changeme"),
        )

    def load_latest_feedback_events(self) -> pd.DataFrame:
        query = """
        SELECT
            f.id AS feedback_id,
            f.alert_id,
            f.user_id,
            f.label AS feedback_label,
            COALESCE(f.reason, '') AS feedback_reason,
            f.created_at AS feedback_created_at,
            a.alert_type,
            a.severity,
            a.source,
            a.confidence,
            a.description,
            a.log_ids,
            a.ip::text AS alert_ip,
            a.timestamp AS alert_timestamp
        FROM feedback f
        JOIN alerts a ON a.id = f.alert_id
        ORDER BY f.created_at DESC, f.id DESC
        """
        with self._connect_db() as conn:
            feedback_events = pd.read_sql(query, conn)
        if feedback_events.empty:
            return feedback_events
        feedback_events["feedback_created_at"] = pd.to_datetime(feedback_events["feedback_created_at"], utc=True, errors="coerce")
        feedback_events["alert_timestamp"] = pd.to_datetime(feedback_events["alert_timestamp"], utc=True, errors="coerce")
        return deduplicate_latest_feedback(feedback_events)

    def fetch_raw_logs(self, log_ids: list[int]) -> pd.DataFrame:
        if not log_ids:
            return pd.DataFrame()
        query = """
        SELECT id, timestamp, ip::text AS ip, method, endpoint, status, response_time_ms, user_agent, data
        FROM raw_logs
        WHERE id = ANY(%s)
        ORDER BY timestamp
        """
        with self._connect_db() as conn:
            raw_logs = pd.read_sql(query, conn, params=(log_ids,))
        if not raw_logs.empty:
            raw_logs["timestamp"] = pd.to_datetime(raw_logs["timestamp"], utc=True, errors="coerce")
        return raw_logs

    def fetch_hybrid_scores(self, log_ids: list[int]) -> pd.DataFrame:
        if not log_ids:
            return pd.DataFrame(columns=["log_id"])
        query = """
        SELECT log_id, rule_score, ml_score, final_score, severity AS current_hybrid_severity,
               triggered_rules, ml_confidence, is_anomaly, created_at AS hybrid_created_at
        FROM hybrid_scores
        WHERE log_id = ANY(%s)
        """
        with self._connect_db() as conn:
            scores = pd.read_sql(query, conn, params=(log_ids,))
        if not scores.empty:
            scores["hybrid_created_at"] = pd.to_datetime(scores["hybrid_created_at"], utc=True, errors="coerce")
        return scores

    def _load_feature_names(self, kind: str) -> list[str]:
        if kind == "iforest":
            feature_file = self.data_dir / "iforest_features.txt"
        else:
            feature_file = self.data_dir / "selected_features.txt"
        if feature_file.exists():
            return [line.strip() for line in feature_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        raise FileNotFoundError(f"Feature list not found: {feature_file}")

    def _load_feature_artifact(self, kind: str) -> pd.DataFrame:
        if kind == "iforest":
            return load_pickle_with_csv_fallback(
                self.data_dir / "ml_dataset_iforest.pkl",
                self.data_dir / "ml_dataset_iforest.csv",
                parse_dates=["timestamp"],
            )
        return load_pickle_with_csv_fallback(
            self.data_dir / "ml_dataset.pkl",
            self.data_dir / "ml_dataset.csv",
            parse_dates=["timestamp"],
        )

    def _load_scaler(self, kind: str, feature_columns: list[str]):
        candidate_names = ["iforest_scaler.pkl", "scaler.pkl"] if kind == "iforest" else ["scaler.pkl", "iforest_scaler.pkl"]
        expected_features = list(feature_columns)
        fallback_scaler = None

        for scaler_name in candidate_names:
            scaler_path = self.models_dir / scaler_name
            if not scaler_path.exists():
                continue
            with open(scaler_path, "rb") as handle:
                scaler = pickle.load(handle)
            scaler_features = list(getattr(scaler, "feature_names_in_", []))
            if scaler_features == expected_features:
                return scaler
            if fallback_scaler is None and not scaler_features:
                fallback_scaler = scaler

        if fallback_scaler is not None:
            return fallback_scaler

        raise ValueError(
            f"No scaler artifact matched {kind} feature schema with {len(expected_features)} features: {expected_features}"
        )

    def _feature_engineer(self) -> FeatureEngineer:
        return FeatureEngineer.__new__(FeatureEngineer)

    def _build_live_feature_rows(self, raw_logs: pd.DataFrame, kind: str, feature_columns: list[str]) -> pd.DataFrame:
        if raw_logs.empty:
            return pd.DataFrame(columns=["log_id", "timestamp", "feature_source", *feature_columns])
        engineer = self._feature_engineer()
        raw_logs = raw_logs.sort_values("timestamp").reset_index(drop=True)
        feature_frame, prepared_logs = FeatureEngineer.extract_features(engineer, raw_logs)
        selected = feature_frame.reindex(columns=feature_columns, fill_value=0.0)
        selected = selected.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(0.0)
        scaler = self._load_scaler(kind, feature_columns)
        scaled = pd.DataFrame(scaler.transform(selected), columns=feature_columns, index=selected.index)
        scaled["log_id"] = prepared_logs["id"].astype(int).values
        scaled["timestamp"] = pd.to_datetime(prepared_logs["timestamp"], utc=True, errors="coerce").values
        scaled["feature_source"] = "live_extract"
        scaled["original_dataset_label"] = np.nan
        return scaled[["log_id", "timestamp", "feature_source", "original_dataset_label", *feature_columns]]

    def build_feature_rows(self, log_ids: list[int], kind: str) -> tuple[pd.DataFrame, list[str]]:
        feature_columns = self._load_feature_names(kind)
        artifact = self._load_feature_artifact(kind)
        artifact_rows = artifact[artifact["log_id"].isin(log_ids)].copy() if not artifact.empty else pd.DataFrame()

        for column in feature_columns:
            if column not in artifact_rows.columns:
                artifact_rows[column] = 0.0
        if "timestamp" in artifact_rows.columns:
            artifact_rows["timestamp"] = pd.to_datetime(artifact_rows["timestamp"], utc=True, errors="coerce")

        artifact_subset = pd.DataFrame(columns=["log_id", "timestamp", "feature_source", "original_dataset_label", *feature_columns])
        if not artifact_rows.empty:
            artifact_subset = artifact_rows[["log_id", "timestamp", *feature_columns]].copy()
            artifact_subset["feature_source"] = "artifact_dataset"
            artifact_subset["original_dataset_label"] = artifact_rows.get("label", pd.Series([np.nan] * len(artifact_subset))).values
            artifact_subset = artifact_subset[["log_id", "timestamp", "feature_source", "original_dataset_label", *feature_columns]]

        found_ids = set(artifact_subset["log_id"].astype(int).tolist()) if not artifact_subset.empty else set()
        missing_ids = sorted({int(log_id) for log_id in log_ids} - found_ids)
        if missing_ids:
            raw_logs = self.fetch_raw_logs(missing_ids)
            live_subset = self._build_live_feature_rows(raw_logs, kind, feature_columns)
            combined = pd.concat([artifact_subset, live_subset], ignore_index=True, sort=False)
        else:
            combined = artifact_subset

        if combined.empty:
            combined = pd.DataFrame(columns=["log_id", "timestamp", "feature_source", "original_dataset_label", *feature_columns])
        combined = combined.drop_duplicates(subset=["log_id"], keep="first").reset_index(drop=True)
        return combined, feature_columns

    def _prepare_review_rows(self, exploded_feedback: pd.DataFrame) -> pd.DataFrame:
        review_rows = exploded_feedback.copy()
        mapped = review_rows["feedback_label"].apply(map_feedback_label_to_training_target)
        review_rows["reviewed_label"] = [item[0] for item in mapped]
        review_rows["training_eligible"] = [item[1] for item in mapped]
        review_rows["exclude_reason"] = [item[2] for item in mapped]
        review_rows["log_id"] = pd.to_numeric(review_rows["log_id"], errors="coerce")
        review_rows["log_id"] = review_rows["log_id"].astype("Int64")
        return review_rows

    def _merge_reviewed_dataset(
        self,
        review_rows: pd.DataFrame,
        feature_rows: pd.DataFrame,
        feature_columns: list[str],
        hybrid_scores: pd.DataFrame,
    ) -> pd.DataFrame:
        merged = review_rows.merge(feature_rows, on="log_id", how="left")
        merged = merged.merge(hybrid_scores, on="log_id", how="left")
        merged["timestamp"] = pd.to_datetime(merged["timestamp"], utc=True, errors="coerce")
        merged["feature_available"] = merged[feature_columns].notna().all(axis=1) if feature_columns else False
        merged["ready_for_training"] = merged["training_eligible"] & merged["feature_available"]
        missing_mask = merged["training_eligible"] & ~merged["feature_available"] & merged["exclude_reason"].isna()
        merged.loc[missing_mask, "exclude_reason"] = "missing_features"
        merged["reviewed_label"] = pd.to_numeric(merged["reviewed_label"], errors="coerce")
        return merged

    def _build_summary(self, review_rows: pd.DataFrame, supervised_dataset: pd.DataFrame) -> dict[str, Any]:
        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "latest_feedback_events": int(review_rows["feedback_id"].nunique()) if not review_rows.empty else 0,
            "reviewed_log_links": int(review_rows["log_id"].notna().sum()) if not review_rows.empty else 0,
            "unique_reviewed_logs": int(review_rows["log_id"].dropna().nunique()) if not review_rows.empty else 0,
            "eligible_feedback_events": int(supervised_dataset.loc[supervised_dataset["training_eligible"], "feedback_id"].nunique()) if not supervised_dataset.empty else 0,
            "ready_feedback_events": int(supervised_dataset.loc[supervised_dataset["ready_for_training"], "feedback_id"].nunique()) if not supervised_dataset.empty else 0,
            "training_eligible_log_samples": int(supervised_dataset["training_eligible"].sum()) if not supervised_dataset.empty else 0,
            "ready_for_training_log_samples": int(supervised_dataset["ready_for_training"].sum()) if not supervised_dataset.empty else 0,
            "deferred_false_negative_events": int(review_rows.loc[review_rows["feedback_label"] == "false_negative", "feedback_id"].nunique()) if not review_rows.empty else 0,
            "logs_with_feedback_conflicts": int(supervised_dataset.loc[supervised_dataset["had_feedback_conflict"], "log_id"].nunique()) if not supervised_dataset.empty else 0,
            "feature_source_counts": {
                str(key): int(value)
                for key, value in supervised_dataset["feature_source"].fillna("missing").value_counts().to_dict().items()
            } if not supervised_dataset.empty else {},
            "ready_label_distribution": {
                str(int(key)): int(value)
                for key, value in supervised_dataset.loc[supervised_dataset["ready_for_training"], "reviewed_label"].value_counts(dropna=True).to_dict().items()
            } if not supervised_dataset.empty else {},
            "runtime_note": RUNTIME_NOTE,
        }
        return summary

    def save_dataset_artifacts(self, supervised_dataset: pd.DataFrame, iforest_dataset: pd.DataFrame, summary: dict[str, Any]) -> None:
        supervised_dataset.to_pickle(self.reviewed_dataset_path)
        supervised_dataset.to_csv(self.reviewed_dataset_csv_path, index=False)
        iforest_dataset.to_pickle(self.reviewed_iforest_path)
        iforest_dataset.to_csv(self.reviewed_iforest_csv_path, index=False)
        self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    def build_and_save(self) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        feedback_events = self.load_latest_feedback_events()
        if feedback_events.empty:
            empty = pd.DataFrame()
            summary = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "latest_feedback_events": 0,
                "reviewed_log_links": 0,
                "unique_reviewed_logs": 0,
                "eligible_feedback_events": 0,
                "ready_feedback_events": 0,
                "training_eligible_log_samples": 0,
                "ready_for_training_log_samples": 0,
                "deferred_false_negative_events": 0,
                "logs_with_feedback_conflicts": 0,
                "feature_source_counts": {},
                "ready_label_distribution": {},
                "runtime_note": RUNTIME_NOTE,
            }
            self.save_dataset_artifacts(empty, empty, summary)
            return empty, empty, summary

        exploded_feedback = explode_feedback_events(feedback_events)
        review_rows = self._prepare_review_rows(exploded_feedback)
        log_ids = [int(value) for value in review_rows["log_id"].dropna().tolist()]
        supervised_features, supervised_feature_columns = self.build_feature_rows(log_ids, kind="supervised")
        iforest_features, iforest_feature_columns = self.build_feature_rows(log_ids, kind="iforest")
        hybrid_scores = self.fetch_hybrid_scores(log_ids)

        supervised_dataset = self._merge_reviewed_dataset(review_rows, supervised_features, supervised_feature_columns, hybrid_scores)
        iforest_dataset = self._merge_reviewed_dataset(review_rows, iforest_features, iforest_feature_columns, hybrid_scores)

        summary = self._build_summary(review_rows, supervised_dataset)
        self.save_dataset_artifacts(supervised_dataset, iforest_dataset, summary)
        return supervised_dataset, iforest_dataset, summary

class FeedbackRetrainingPipeline:
    """Train and evaluate a reviewed-feedback candidate model."""

    def __init__(
        self,
        mlflow_uri: str = DEFAULT_MLFLOW_URI,
        experiment_name: str = DEFAULT_EXPERIMENT_NAME,
        split_config: SplitConfig = SplitConfig(),
        promotion_config: PromotionConfig = PromotionConfig(),
        data_dir: str = "data",
        models_dir: str = "models",
        experiments_dir: str = "experiments",
    ):
        self.split_config = split_config
        self.promotion_config = promotion_config
        self.builder = ReviewedFeedbackBuilder(data_dir=data_dir, models_dir=models_dir)
        self.data_dir = Path(data_dir)
        self.models_dir = Path(models_dir)
        self.experiments_dir = Path(experiments_dir)
        self.experiments_dir.mkdir(parents=True, exist_ok=True)
        self.mlflow_uri = mlflow_uri
        self.experiment_name = experiment_name
        try:
            mlflow.set_tracking_uri(mlflow_uri)
            mlflow.set_experiment(experiment_name)
        except Exception as exc:
            fallback_uri = f"file:{Path('mlruns').resolve().as_posix()}"
            self.mlflow_uri = fallback_uri
            mlflow.set_tracking_uri(fallback_uri)
            mlflow.set_experiment(experiment_name)
            print(f"MLflow fallback enabled because remote tracking failed: {exc}")

    def _current_model_paths(self) -> tuple[Path, Path]:
        model_path = self.models_dir / "random_forest_latest.pkl"
        metadata_path = self.models_dir / "random_forest_latest_metadata.json"
        return model_path, metadata_path

    def _candidate_model_paths(self) -> tuple[Path, Path]:
        model_path = self.models_dir / "random_forest_feedback_candidate.pkl"
        metadata_path = self.models_dir / "random_forest_feedback_candidate_metadata.json"
        return model_path, metadata_path

    def load_current_baseline(self) -> tuple[Any | None, dict[str, Any] | None]:
        model_path, metadata_path = self._current_model_paths()
        if not model_path.exists():
            return None, None
        with open(model_path, "rb") as handle:
            model = pickle.load(handle)
        metadata = None
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return model, metadata

    def get_candidate_configs(self) -> list[CandidateConfig]:
        return [
            CandidateConfig(200, None, (0.40, 0.45, 0.50, 0.55, 0.60)),
            CandidateConfig(300, "balanced_subsample", (0.20, 0.25, 0.30, 0.35, 0.40)),
            CandidateConfig(500, "balanced_subsample", (0.20, 0.25, 0.30, 0.35, 0.40)),
        ]

    def fit_candidate(self, x_train: pd.DataFrame, y_train: pd.Series, config: CandidateConfig) -> RandomForestClassifier:
        model = RandomForestClassifier(
            n_estimators=config.n_estimators,
            random_state=42,
            n_jobs=-1,
            class_weight=config.class_weight,
        )
        model.fit(x_train, y_train)
        return model

    def score_model(self, model: Any, x_frame: pd.DataFrame) -> np.ndarray:
        return model.predict_proba(x_frame)[:, 1]

    def select_best_candidate(
        self,
        train_df: pd.DataFrame,
        reviewed_holdout: pd.DataFrame,
        temporal_holdout: pd.DataFrame,
        feature_columns: list[str],
    ) -> dict[str, Any]:
        x_train = train_df[feature_columns]
        y_train = train_df["reviewed_label"].astype(int)
        x_reviewed = reviewed_holdout[feature_columns]
        y_reviewed = reviewed_holdout["reviewed_label"].astype(int)
        x_temporal = temporal_holdout[feature_columns]
        y_temporal = temporal_holdout["reviewed_label"].astype(int)

        best: dict[str, Any] | None = None
        for config in self.get_candidate_configs():
            model = self.fit_candidate(x_train, y_train, config)
            reviewed_scores = self.score_model(model, x_reviewed)
            threshold_info = find_best_threshold(reviewed_scores, y_reviewed, list(config.thresholds))
            reviewed_metrics, reviewed_pred = evaluate_binary_scores(reviewed_scores, y_reviewed, threshold_info["threshold"])
            temporal_scores = self.score_model(model, x_temporal)
            temporal_metrics, temporal_pred = evaluate_binary_scores(temporal_scores, y_temporal, threshold_info["threshold"])
            candidate = {
                "model": model,
                "config": {
                    "n_estimators": config.n_estimators,
                    "class_weight": config.class_weight,
                    "thresholds": list(config.thresholds),
                },
                "threshold": threshold_info["threshold"],
                "reviewed_holdout": reviewed_metrics,
                "temporal_holdout": temporal_metrics,
                "reviewed_predictions": reviewed_pred.tolist(),
                "temporal_predictions": temporal_pred.tolist(),
            }
            key = (
                candidate["reviewed_holdout"]["f1_score"],
                candidate["temporal_holdout"]["f1_score"],
                candidate["reviewed_holdout"]["precision"],
                candidate["temporal_holdout"]["precision"],
            )
            if best is None or key > best["key"]:
                best = {"candidate": candidate, "key": key}
        if best is None:
            raise RuntimeError("No candidate model could be trained")
        return best["candidate"]

    def evaluate_baseline(
        self,
        model: Any | None,
        metadata: dict[str, Any] | None,
        reviewed_holdout: pd.DataFrame,
        temporal_holdout: pd.DataFrame,
        feature_columns: list[str],
    ) -> dict[str, Any] | None:
        if model is None:
            return None
        threshold = float((metadata or {}).get("threshold", 0.5))
        reviewed_scores = self.score_model(model, reviewed_holdout[feature_columns])
        temporal_scores = self.score_model(model, temporal_holdout[feature_columns])
        reviewed_metrics, reviewed_pred = evaluate_binary_scores(reviewed_scores, reviewed_holdout["reviewed_label"].astype(int), threshold)
        temporal_metrics, temporal_pred = evaluate_binary_scores(temporal_scores, temporal_holdout["reviewed_label"].astype(int), threshold)
        return {
            "threshold": threshold,
            "saved_at": (metadata or {}).get("saved_at"),
            "reviewed_holdout": reviewed_metrics,
            "temporal_holdout": temporal_metrics,
            "reviewed_predictions": reviewed_pred.tolist(),
            "temporal_predictions": temporal_pred.tolist(),
        }

    def _count_new_feedback_since(self, dataset: pd.DataFrame, saved_at: str | None) -> tuple[int, int]:
        if not saved_at:
            return 0, 0
        saved_at_ts = pd.to_datetime(saved_at, utc=True, errors="coerce")
        if pd.isna(saved_at_ts):
            return 0, 0
        new_feedback_mask = dataset["feedback_created_at"] > saved_at_ts
        new_feedback_events = int(dataset.loc[new_feedback_mask, "feedback_id"].nunique())
        new_training_rows = int(dataset.loc[new_feedback_mask & dataset["ready_for_training"]].shape[0])
        return new_feedback_events, new_training_rows

    def save_candidate_model(
        self,
        model: Any,
        threshold: float,
        feature_columns: list[str],
        dataset_summary: dict[str, Any],
        candidate_metrics: dict[str, Any],
        promotion: dict[str, Any],
    ) -> tuple[Path, Path]:
        model_path, metadata_path = self._candidate_model_paths()
        with open(model_path, "wb") as handle:
            pickle.dump(model, handle)
        metadata = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "threshold": threshold,
            "tracking_uri": self.mlflow_uri,
            "model_family": "random_forest_feedback_candidate",
            "feature_columns": feature_columns,
            "dataset_summary": dataset_summary,
            "candidate_metrics": candidate_metrics,
            "promotion": promotion,
            "runtime_note": RUNTIME_NOTE,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return model_path, metadata_path

    def write_report(self, report: dict[str, Any]) -> Path:
        report_path = self.experiments_dir / "feedback_retraining_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report_path

    def sync_runtime_metrics(
        self,
        report: dict[str, Any],
        dataset_summary: dict[str, Any],
        baseline: dict[str, Any] | None,
        candidate: dict[str, Any] | None,
        promotion: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = build_retraining_runtime_metrics_payload(report, dataset_summary, baseline, candidate, promotion)
        persist_runtime_metrics(payload)
        try:
            refresh_monitoring_metrics(force=True)
        except Exception as exc:
            print(f"Monitoring refresh skipped after retraining report update: {exc}")
        return payload

    def finalize_report(
        self,
        report: dict[str, Any],
        dataset_summary: dict[str, Any],
        split_summary: dict[str, dict[str, int]] | None,
        baseline: dict[str, Any] | None,
        candidate: dict[str, Any] | None,
        promotion: dict[str, Any] | None,
        candidate_model_path: Path | None,
        candidate_metadata_path: Path | None,
    ) -> dict[str, Any]:
        report["runtime_metrics"] = self.sync_runtime_metrics(report, dataset_summary, baseline, candidate, promotion)
        report_path = self.write_report(report)
        self.log_to_mlflow(
            dataset_summary,
            split_summary,
            baseline,
            candidate,
            promotion,
            report_path,
            candidate_model_path,
            candidate_metadata_path,
        )
        return report

    def log_to_mlflow(
        self,
        dataset_summary: dict[str, Any],
        split_summary: dict[str, dict[str, int]] | None,
        baseline: dict[str, Any] | None,
        candidate: dict[str, Any] | None,
        promotion: dict[str, Any] | None,
        report_path: Path,
        candidate_model_path: Path | None,
        candidate_metadata_path: Path | None,
    ) -> None:
        if mlflow.active_run() is not None:
            mlflow.end_run()
        run_name = f"feedback_retraining_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        with mlflow.start_run(run_name=run_name):
            mlflow.log_param("runtime_note", RUNTIME_NOTE)
            mlflow.log_param("split_config", json.dumps(asdict(self.split_config), sort_keys=True))
            mlflow.log_param("promotion_config", json.dumps(asdict(self.promotion_config), sort_keys=True))
            for key, value in dataset_summary.items():
                if isinstance(value, bool):
                    mlflow.log_metric(f"dataset_{key}", int(value))
                elif isinstance(value, (int, float)):
                    mlflow.log_metric(f"dataset_{key}", value)
            if split_summary is not None:
                for split_name, split_values in split_summary.items():
                    for key, value in split_values.items():
                        mlflow.log_metric(f"split_{split_name}_{key}", value)
            if baseline is not None:
                mlflow.log_param("baseline_available", True)
                for split_name in ("reviewed_holdout", "temporal_holdout"):
                    for metric_name, metric_value in baseline[split_name].items():
                        mlflow.log_metric(f"baseline_{split_name}_{metric_name}", metric_value)
            else:
                mlflow.log_param("baseline_available", False)
            if candidate is not None:
                mlflow.log_param("candidate_config", json.dumps(candidate["config"], sort_keys=True))
                for split_name in ("reviewed_holdout", "temporal_holdout"):
                    for metric_name, metric_value in candidate[split_name].items():
                        mlflow.log_metric(f"candidate_{split_name}_{metric_name}", metric_value)
            if promotion is not None:
                mlflow.log_metric("promotion_promotable", int(promotion["promotable"]))
                for check_name, passed in promotion["checks"].items():
                    mlflow.log_metric(f"promotion_{check_name}", int(passed))
            artifact_paths = [
                self.builder.reviewed_dataset_csv_path,
                self.builder.summary_path,
                report_path,
            ]
            if candidate_model_path is not None:
                artifact_paths.append(candidate_model_path)
            if candidate_metadata_path is not None:
                artifact_paths.append(candidate_metadata_path)
            for artifact_path in artifact_paths:
                try:
                    mlflow.log_artifact(str(artifact_path))
                except Exception as exc:
                    print(f"MLflow artifact logging skipped for {artifact_path}: {exc}")

    def run(self) -> dict[str, Any]:
        supervised_dataset, _, dataset_summary = self.builder.build_and_save()
        report: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime_note": RUNTIME_NOTE,
            "dataset": dataset_summary,
        }

        if supervised_dataset.empty:
            report["status"] = "blocked_no_feedback"
            report["message"] = "No feedback rows were available to build a reviewed dataset."
            return self.finalize_report(report, dataset_summary, None, None, None, None, None, None)

        current_model, current_metadata = self.load_current_baseline()
        new_feedback_events, new_training_rows = self._count_new_feedback_since(
            supervised_dataset,
            (current_metadata or {}).get("saved_at") if current_metadata else None,
        )
        dataset_summary["new_feedback_events_since_current_model"] = new_feedback_events
        dataset_summary["new_training_rows_since_current_model"] = new_training_rows
        self.builder.summary_path.write_text(json.dumps(dataset_summary, indent=2), encoding="utf-8")

        training_ready = supervised_dataset.loc[supervised_dataset["ready_for_training"]].copy()
        if training_ready.empty:
            report["status"] = "blocked_no_trainable_feedback"
            report["message"] = "Feedback exists, but none of the reviewed rows are currently ready for training."
            return self.finalize_report(report, dataset_summary, None, None, None, None, None, None)

        feature_columns = self.builder._load_feature_names("supervised")
        try:
            splits = build_reviewed_splits(training_ready, split_config=self.split_config)
        except ValueError as exc:
            report["status"] = "blocked_split_error"
            report["message"] = str(exc)
            return self.finalize_report(report, dataset_summary, None, None, None, None, None, None)

        split_summary = {name: summarize_split(frame) for name, frame in splits.items()}
        baseline = self.evaluate_baseline(current_model, current_metadata, splits["reviewed_holdout"], splits["temporal_holdout"], feature_columns)
        candidate = self.select_best_candidate(splits["train"], splits["reviewed_holdout"], splits["temporal_holdout"], feature_columns)
        promotion = build_promotion_decision(dataset_summary, split_summary, baseline, candidate, self.promotion_config)
        candidate_model_path, candidate_metadata_path = self.save_candidate_model(
            candidate["model"],
            candidate["threshold"],
            feature_columns,
            dataset_summary,
            {
                "config": candidate["config"],
                "reviewed_holdout": candidate["reviewed_holdout"],
                "temporal_holdout": candidate["temporal_holdout"],
            },
            promotion,
        )

        report.update(
            {
                "status": "candidate_built",
                "splits": split_summary,
                "baseline": baseline,
                "candidate": {
                    "config": candidate["config"],
                    "threshold": candidate["threshold"],
                    "reviewed_holdout": candidate["reviewed_holdout"],
                    "temporal_holdout": candidate["temporal_holdout"],
                    "model_path": str(candidate_model_path),
                    "metadata_path": str(candidate_metadata_path),
                },
                "promotion": promotion,
            }
        )
        return self.finalize_report(
            report,
            dataset_summary,
            split_summary,
            baseline,
            candidate,
            promotion,
            candidate_model_path,
            candidate_metadata_path,
        )


def main() -> dict[str, Any]:
    pipeline = FeedbackRetrainingPipeline()
    results = pipeline.run()
    print("=" * 72)
    print("FEEDBACK RETRAINING REPORT")
    print("=" * 72)
    print(f"status: {results['status']}")
    dataset = results.get("dataset", {})
    print(f"eligible_feedback_events: {dataset.get('eligible_feedback_events', 0)}")
    print(f"ready_for_training_log_samples: {dataset.get('ready_for_training_log_samples', 0)}")
    promotion = results.get("promotion")
    if promotion:
        print(f"promotable: {promotion['promotable']}")
        if promotion["failed_checks"]:
            print(f"failed_checks: {', '.join(promotion['failed_checks'])}")
    print("report: experiments/feedback_retraining_report.json")
    return results


if __name__ == "__main__":
    main()

