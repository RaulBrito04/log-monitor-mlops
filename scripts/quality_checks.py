#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATASET_CANDIDATES = [
    Path("data/reviewed_feedback_dataset.csv"),
    Path("data/ml_dataset.csv"),
    Path("data/reviewed_feedback_dataset.pkl"),
    Path("data/ml_dataset.pkl"),
]
LABEL_CANDIDATES = ("reviewed_label", "label")
TIMESTAMP_CANDIDATES = ("timestamp", "created_at")


def _load_frame(path: Path) -> pd.DataFrame:
    if path.suffix == ".csv":
        return pd.read_csv(path)
    if path.suffix == ".pkl":
        return pd.read_pickle(path)
    raise ValueError(f"Unsupported dataset extension: {path.suffix}")


def load_quality_dataset() -> tuple[pd.DataFrame | None, Path | None, str | None]:
    errors: list[str] = []
    for candidate in DATASET_CANDIDATES:
        if not candidate.exists():
            continue
        try:
            frame = _load_frame(candidate)
        except (NotImplementedError, ModuleNotFoundError, AttributeError, ValueError) as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        return frame, candidate, None
    return None, None, "; ".join(errors) if errors else None


def _find_label_column(df: pd.DataFrame) -> str | None:
    for column in LABEL_CANDIDATES:
        if column in df.columns:
            return column
    return None


def _find_timestamp_column(df: pd.DataFrame) -> str | None:
    for column in TIMESTAMP_CANDIDATES:
        if column in df.columns:
            return column
    return None


def build_quality_summary(df: pd.DataFrame) -> list[str]:
    lines = ["QUALITY CHECKS", "=" * 60, f"Rows: {len(df):,}", f"Columns: {len(df.columns):,}"]

    missing = df.isnull().sum()
    missing_total = int(missing.sum())
    if missing_total == 0:
        lines.append("OK No missing values")
    else:
        hot_missing = missing[missing > 0].sort_values(ascending=False).head(10)
        lines.append(f"WARN Missing values found: {missing_total:,}")
        for column, count in hot_missing.items():
            lines.append(f"  - {column}: {int(count):,}")

    numeric = df.select_dtypes(include=[np.number])
    inf_count = int(np.isinf(numeric.to_numpy()).sum()) if not numeric.empty else 0
    if inf_count == 0:
        lines.append("OK No infinite values")
    else:
        lines.append(f"WARN Infinite numeric values found: {inf_count:,}")

    constant_columns = [column for column in df.columns if df[column].nunique(dropna=False) <= 1]
    if not constant_columns:
        lines.append("OK No constant columns")
    else:
        preview = ", ".join(constant_columns[:10])
        suffix = " ..." if len(constant_columns) > 10 else ""
        lines.append(f"WARN Constant columns: {preview}{suffix}")

    duplicate_rows = int(df.duplicated().sum())
    lines.append(f"Duplicate rows: {duplicate_rows:,}")

    label_column = _find_label_column(df)
    if label_column is None:
        lines.append("INFO No label column found; skipped class-balance checks")
    else:
        labels = pd.to_numeric(df[label_column], errors='coerce').dropna()
        if labels.empty:
            lines.append(f"WARN Label column '{label_column}' could not be interpreted numerically")
        else:
            positive_rate = float(labels.mean())
            lines.append(f"Label column: {label_column}")
            lines.append(f"Positive rate: {positive_rate:.2%}")
            if 0.01 < positive_rate < 0.5:
                lines.append("OK Label balance within expected anomaly range")
            else:
                lines.append("WARN Label balance outside expected anomaly range")

    timestamp_column = _find_timestamp_column(df)
    if timestamp_column is None:
        lines.append("INFO No timestamp column found; skipped temporal coverage checks")
    else:
        timestamps = pd.to_datetime(df[timestamp_column], utc=True, errors='coerce').dropna()
        if timestamps.empty:
            lines.append(f"WARN Timestamp column '{timestamp_column}' could not be parsed")
        else:
            lines.append(f"Time range: {timestamps.min()} -> {timestamps.max()}")

    lines.append("=" * 60)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lightweight dataset quality checks for the project artifacts.")
    parser.add_argument("--require-dataset", action="store_true", help="Exit non-zero if no compatible dataset artifact is available.")
    args = parser.parse_args()

    frame, source, error_message = load_quality_dataset()
    if frame is None:
        if args.require_dataset:
            print("ERROR: No compatible dataset artifact was found.")
            if error_message:
                print(error_message)
            return 1
        print("INFO: No compatible dataset artifact was found; dataset-specific checks skipped.")
        if error_message:
            print(f"INFO: Dataset load issues: {error_message}")
        return 0

    print(f"Dataset source: {source}")
    for line in build_quality_summary(frame):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
