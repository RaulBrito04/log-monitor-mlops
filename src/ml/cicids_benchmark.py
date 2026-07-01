#!/usr/bin/env python3
"""External validation benchmark harness for CICIDS-style datasets."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

DEFAULT_REPORT_PATH = Path("experiments/cicids_benchmark_report.json")
DEFAULT_MARKDOWN_PATH = Path("experiments/cicids_benchmark_report.md")
THRESHOLD_QUANTILES = (0.80, 0.84, 0.88, 0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99)


def normalize_column_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower())
    return value.strip("_")


def resolve_csv_paths(inputs: list[str | Path]) -> list[Path]:
    resolved: list[Path] = []
    for raw_path in inputs:
        path = Path(raw_path)
        if path.is_dir():
            resolved.extend(sorted(candidate for candidate in path.rglob("*.csv") if candidate.is_file()))
        elif path.is_file() and path.suffix.lower() == ".csv":
            resolved.append(path)
    unique_paths = list(dict.fromkeys(candidate.resolve() for candidate in resolved))
    if not unique_paths:
        raise FileNotFoundError("No CICIDS CSV files were found in the provided input paths")
    return unique_paths


def load_cicids_frame(inputs: list[str | Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for csv_path in resolve_csv_paths(inputs):
        frame = pd.read_csv(csv_path, low_memory=False)
        frame["source_file"] = csv_path.name
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False)


def normalize_cicids_frame(raw_frame: pd.DataFrame) -> pd.DataFrame:
    frame = raw_frame.rename(columns={column: normalize_column_name(column) for column in raw_frame.columns}).copy()
    if "label" not in frame.columns:
        raise ValueError("CICIDS benchmark requires a label column")

    frame["label"] = frame["label"].astype(str).str.strip()
    frame["benchmark_label"] = np.where(frame["label"].str.upper().eq("BENIGN"), 0, 1).astype(int)
    if "source_file" not in frame.columns:
        frame["source_file"] = "in_memory.csv"
    frame["event_order"] = np.arange(len(frame), dtype=int)

    if "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        if frame["timestamp"].notna().any():
            frame = frame.sort_values(["timestamp", "event_order"]).reset_index(drop=True)
        else:
            frame = frame.sort_values("event_order").reset_index(drop=True)
    else:
        frame["timestamp"] = pd.NaT
        frame = frame.sort_values("event_order").reset_index(drop=True)

    return frame


def select_numeric_features(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    excluded = {"label", "benchmark_label", "timestamp", "source_file", "event_order"}
    numeric = frame[[column for column in frame.columns if column not in excluded]].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.replace([np.inf, -np.inf], np.nan)
    feature_cols = [
        column for column in numeric.columns
        if numeric[column].notna().any() and numeric[column].nunique(dropna=True) > 1
    ]
    if len(feature_cols) < 4:
        raise ValueError("CICIDS benchmark requires at least four usable numeric feature columns")
    return numeric[feature_cols], feature_cols


def fill_numeric(frame: pd.DataFrame, medians: pd.Series | None = None) -> tuple[pd.DataFrame, pd.Series]:
    cleaned = frame.replace([np.inf, -np.inf], np.nan)
    stats = medians if medians is not None else cleaned.median(numeric_only=True)
    cleaned = cleaned.fillna(stats).fillna(0.0)
    return cleaned.astype(float), stats


def build_temporal_splits(frame: pd.DataFrame, train_frac: float = 0.6, validation_frac: float = 0.2) -> dict[str, pd.DataFrame]:
    if not 0 < train_frac < 1 or not 0 < validation_frac < 1 or train_frac + validation_frac >= 1:
        raise ValueError("Invalid split fractions")
    if len(frame) < 15:
        raise ValueError("At least 15 rows are required for CICIDS benchmark splitting")

    train_end = int(len(frame) * train_frac)
    validation_end = int(len(frame) * (train_frac + validation_frac))
    splits = {
        "train": frame.iloc[:train_end].reset_index(drop=True),
        "validation": frame.iloc[train_end:validation_end].reset_index(drop=True),
        "test": frame.iloc[validation_end:].reset_index(drop=True),
    }

    if splits["train"].empty or splits["validation"].empty or splits["test"].empty:
        raise ValueError("Temporal split produced an empty partition")
    if splits["train"]["benchmark_label"].nunique() < 2:
        raise ValueError("Training split must contain both BENIGN and attack rows for benchmark supervision")
    if splits["validation"]["benchmark_label"].sum() == 0 or splits["test"]["benchmark_label"].sum() == 0:
        raise ValueError("Validation and test splits must contain attack rows")
    return splits


def evaluate_scores(scores: np.ndarray, labels: pd.Series | np.ndarray, threshold: float) -> dict[str, float]:
    y_true = np.asarray(labels, dtype=int)
    y_scores = np.asarray(scores, dtype=float)
    y_pred = (y_scores >= threshold).astype(int)
    try:
        roc_auc = roc_auc_score(y_true, y_scores)
    except ValueError:
        roc_auc = 0.5
    return {
        "threshold": float(threshold),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1_score": float(f1_score(y_true, y_pred, zero_division=0)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "roc_auc": float(roc_auc),
        "positives": int(y_true.sum()),
        "negatives": int((y_true == 0).sum()),
        "predicted_positives": int(y_pred.sum()),
    }


def find_best_threshold(scores: np.ndarray, labels: pd.Series | np.ndarray) -> dict[str, float]:
    series = pd.Series(np.asarray(scores, dtype=float))
    candidates = list(series.quantile(THRESHOLD_QUANTILES).astype(float).tolist())
    candidates.extend(np.linspace(float(series.min()), float(series.max()), num=21).tolist())
    best: dict[str, float] | None = None
    for threshold in sorted(set(candidates)):
        metrics = evaluate_scores(series.to_numpy(), labels, threshold)
        key = (metrics["f1_score"], metrics["precision"], metrics["recall"], -threshold)
        if best is None or key > best["key"]:
            best = {**metrics, "key": key}
    if best is None:
        raise ValueError("Could not derive a threshold for CICIDS benchmark")
    best.pop("key", None)
    return best


def normalize_scores(scores: np.ndarray, minimum: float, maximum: float) -> np.ndarray:
    denom = max(maximum - minimum, 1e-9)
    return (np.asarray(scores, dtype=float) - minimum) / denom


def fit_iforest(train_frame: pd.DataFrame, feature_cols: list[str], random_state: int) -> dict[str, Any]:
    benign_train = train_frame[train_frame["benchmark_label"] == 0]
    if benign_train.empty:
        raise ValueError("Isolation Forest benchmark requires BENIGN rows in the train split")
    cleaned_train, medians = fill_numeric(benign_train[feature_cols])
    scaler = StandardScaler()
    scaled_train = scaler.fit_transform(cleaned_train)
    model = IsolationForest(
        n_estimators=200,
        contamination="auto",
        random_state=random_state,
    )
    model.fit(scaled_train)
    return {"model": model, "scaler": scaler, "medians": medians}


def score_iforest(bundle: dict[str, Any], frame: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    cleaned, _ = fill_numeric(frame[feature_cols], bundle["medians"])
    scaled = bundle["scaler"].transform(cleaned)
    return -bundle["model"].decision_function(scaled)


def fit_random_forest(train_frame: pd.DataFrame, feature_cols: list[str], random_state: int) -> dict[str, Any]:
    cleaned_train, medians = fill_numeric(train_frame[feature_cols])
    model = RandomForestClassifier(
        n_estimators=300,
        class_weight="balanced_subsample",
        random_state=random_state,
    )
    model.fit(cleaned_train, train_frame["benchmark_label"])
    return {"model": model, "medians": medians}


def score_random_forest(bundle: dict[str, Any], frame: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    cleaned, _ = fill_numeric(frame[feature_cols], bundle["medians"])
    return bundle["model"].predict_proba(cleaned)[:, 1]


def write_markdown_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataset = report["dataset"]
    models = report["models"]
    lines = [
        "# CICIDS Benchmark Report",
        "",
        "## Context",
        "",
        f"- Source files: {', '.join(dataset['source_files'])}",
        f"- Total rows: {dataset['total_rows']}",
        f"- Attack rows: {dataset['attack_rows']}",
        f"- Benign rows: {dataset['benign_rows']}",
        f"- Numeric feature count: {report['feature_count']}",
        "",
        "## Split Summary",
        "",
        f"- Train rows: {dataset['splits']['train_rows']}",
        f"- Validation rows: {dataset['splits']['validation_rows']}",
        f"- Test rows: {dataset['splits']['test_rows']}",
        "",
        "## Test Metrics",
        "",
        f"- Isolation Forest F1: {models['iforest']['test']['f1_score']:.4f} | ROC-AUC: {models['iforest']['test']['roc_auc']:.4f}",
        f"- Random Forest F1: {models['random_forest']['test']['f1_score']:.4f} | ROC-AUC: {models['random_forest']['test']['roc_auc']:.4f}",
        f"- Ensemble F1: {models['ensemble']['test']['f1_score']:.4f} | ROC-AUC: {models['ensemble']['test']['roc_auc']:.4f}",
        "",
        "## Notes",
        "",
        "- This benchmark is intentionally separate from the runtime HTTP log pipeline.",
        "- CICIDS-2017 validates the anomaly-detection method family on a public intrusion dataset.",
        "- It does not replace the real operational log validation already closed through Nginx access logs.",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark(
    inputs: list[str | Path],
    report_path: str | Path = DEFAULT_REPORT_PATH,
    markdown_path: str | Path | None = DEFAULT_MARKDOWN_PATH,
    random_state: int = 42,
) -> dict[str, Any]:
    raw_frame = load_cicids_frame(inputs)
    normalized_frame = normalize_cicids_frame(raw_frame)
    numeric_features, feature_cols = select_numeric_features(normalized_frame)
    dataset = normalized_frame[["label", "benchmark_label", "timestamp", "source_file", "event_order"]].join(numeric_features)
    splits = build_temporal_splits(dataset)

    iforest_bundle = fit_iforest(splits["train"], feature_cols, random_state=random_state)
    rf_bundle = fit_random_forest(splits["train"], feature_cols, random_state=random_state)

    if_validation_scores = score_iforest(iforest_bundle, splits["validation"], feature_cols)
    if_test_scores = score_iforest(iforest_bundle, splits["test"], feature_cols)
    if_validation = find_best_threshold(if_validation_scores, splits["validation"]["benchmark_label"])
    if_test = evaluate_scores(if_test_scores, splits["test"]["benchmark_label"], if_validation["threshold"])

    rf_validation_scores = score_random_forest(rf_bundle, splits["validation"], feature_cols)
    rf_test_scores = score_random_forest(rf_bundle, splits["test"], feature_cols)
    rf_validation = find_best_threshold(rf_validation_scores, splits["validation"]["benchmark_label"])
    rf_test = evaluate_scores(rf_test_scores, splits["test"]["benchmark_label"], rf_validation["threshold"])

    if_val_min = float(np.min(if_validation_scores))
    if_val_max = float(np.max(if_validation_scores))
    if_validation_norm = normalize_scores(if_validation_scores, if_val_min, if_val_max)
    if_test_norm = normalize_scores(if_test_scores, if_val_min, if_val_max)
    ensemble_validation_scores = 0.5 * if_validation_norm + 0.5 * rf_validation_scores
    ensemble_test_scores = 0.5 * if_test_norm + 0.5 * rf_test_scores
    ensemble_validation = find_best_threshold(ensemble_validation_scores, splits["validation"]["benchmark_label"])
    ensemble_test = evaluate_scores(ensemble_test_scores, splits["test"]["benchmark_label"], ensemble_validation["threshold"])

    report = {
        "dataset": {
            "source_files": sorted(dataset["source_file"].dropna().astype(str).unique().tolist()),
            "total_rows": int(len(dataset)),
            "attack_rows": int(dataset["benchmark_label"].sum()),
            "benign_rows": int((dataset["benchmark_label"] == 0).sum()),
            "splits": {
                "train_rows": int(len(splits["train"])),
                "validation_rows": int(len(splits["validation"])),
                "test_rows": int(len(splits["test"])),
            },
        },
        "feature_count": int(len(feature_cols)),
        "feature_sample": feature_cols[:12],
        "models": {
            "iforest": {"validation": if_validation, "test": if_test},
            "random_forest": {"validation": rf_validation, "test": rf_test},
            "ensemble": {"validation": ensemble_validation, "test": ensemble_test},
        },
        "notes": [
            "CICIDS benchmark is separate from the deployed HTTP log feature pipeline.",
            "Use this report as external methodological validation, not as operational ingestion evidence.",
        ],
    }

    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if markdown_path is not None:
        write_markdown_report(report, Path(markdown_path))

    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an external CICIDS benchmark without touching the runtime HTTP pipeline")
    parser.add_argument("--input", action="append", required=True, help="CSV file or directory containing CICIDS-style CSV files")
    parser.add_argument("--report-path", default=str(DEFAULT_REPORT_PATH), help="JSON output path")
    parser.add_argument("--markdown-path", default=str(DEFAULT_MARKDOWN_PATH), help="Markdown summary path")
    parser.add_argument("--random-state", type=int, default=42, help="Random seed for benchmark reproducibility")
    return parser


def main() -> dict[str, Any]:
    args = build_arg_parser().parse_args()
    report = run_benchmark(
        inputs=args.input,
        report_path=args.report_path,
        markdown_path=args.markdown_path,
        random_state=args.random_state,
    )
    print(json.dumps({
        "report_path": str(args.report_path),
        "markdown_path": str(args.markdown_path),
        "ensemble_test_f1": report["models"]["ensemble"]["test"]["f1_score"],
        "iforest_test_f1": report["models"]["iforest"]["test"]["f1_score"],
    }, indent=2))
    return report


if __name__ == "__main__":
    main()

