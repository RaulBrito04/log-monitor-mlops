from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import pickle
from typing import Any

import numpy as np
import pandas as pd

from src.ml.feature_engineering import FeatureEngineer


def _safe_load_pickle(path: str | Path, allowed_dir: str | Path = "models"):
    allowed_root = Path(allowed_dir).resolve()
    resolved = Path(path).resolve()

    if allowed_root not in resolved.parents and resolved != allowed_root:
        raise ValueError(
            f"Artifact outside the allowed directory: {path}. "
            f"Only artifacts inside {allowed_root} may be loaded."
        )

    if resolved.suffix != ".pkl":
        raise ValueError(f"Invalid artifact extension: {resolved.suffix}")

    if not resolved.exists():
        raise FileNotFoundError(f"Artifact not found: {resolved}")

    with open(resolved, "rb") as handle:
        return pickle.load(handle)


class RandomForestReferenceExplainerBase:
    """Shared dataset and feature-row helpers for local RF explanations."""

    def __init__(
        self,
        model_path: str | Path = "models/random_forest_latest.pkl",
        features_path: str | Path = "data/selected_features.txt",
        dataset_paths: list[str | Path] | None = None,
        allowed_model_dir: str | Path = "models",
    ) -> None:
        self.model = _safe_load_pickle(model_path, allowed_dir=allowed_model_dir)
        if not hasattr(self.model, "predict_proba"):
            raise ValueError("This explainer requires a classifier with predict_proba().")

        self.feature_names = self._load_feature_names(features_path)
        self.reference_frame = self._load_reference_frame(dataset_paths)
        self.reference_features = self._prepare_feature_frame(self.reference_frame)
        self.feature_medians = self.reference_features.median(numeric_only=True).to_dict()
        stds = self.reference_features[self.feature_names].std(ddof=0).replace(0.0, np.nan)
        self.feature_stds = stds.fillna(1.0).to_dict()
        self.reference_label_column = self._resolve_label_column(self.reference_frame)

    def _load_feature_names(self, features_path: str | Path) -> list[str]:
        path = Path(features_path)
        if not path.exists():
            raise FileNotFoundError(f"Feature list not found: {path}")
        feature_names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not feature_names:
            raise ValueError(f"Feature list is empty: {path}")
        return feature_names

    def _default_dataset_paths(self) -> list[Path]:
        return [
            Path("data/reviewed_feedback_dataset.csv"),
            Path("data/ml_dataset.csv"),
            Path("data/reviewed_feedback_dataset.pkl"),
            Path("data/ml_dataset.pkl"),
        ]

    def _read_dataset(self, dataset_path: Path) -> pd.DataFrame:
        if dataset_path.suffix == ".csv":
            frame = pd.read_csv(dataset_path)
            if "timestamp" in frame.columns:
                frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
            return frame
        if dataset_path.suffix == ".pkl":
            try:
                return pd.read_pickle(dataset_path)
            except (NotImplementedError, ModuleNotFoundError, AttributeError) as exc:
                raise ValueError(f"Could not load pickle dataset {dataset_path}: {exc}") from exc
        raise ValueError(f"Unsupported dataset extension: {dataset_path.suffix}")

    def _load_reference_frame(self, dataset_paths: list[str | Path] | None) -> pd.DataFrame:
        candidates = [Path(path) for path in dataset_paths] if dataset_paths else self._default_dataset_paths()
        errors: list[str] = []
        frames: list[pd.DataFrame] = []
        for candidate in candidates:
            if not candidate.exists():
                continue
            try:
                frame = self._read_dataset(candidate)
            except ValueError as exc:
                errors.append(str(exc))
                continue

            if not set(self.feature_names).issubset(frame.columns):
                errors.append(f"{candidate} does not include the required supervised features.")
                continue

            loaded = frame.copy()
            loaded["_reference_dataset"] = candidate.name
            frames.append(loaded)

        if not frames:
            message = "; ".join(errors) if errors else "No compatible reference dataset was found."
            raise FileNotFoundError(message)

        combined = pd.concat(frames, ignore_index=True, sort=False)
        if "log_id" in combined.columns:
            combined = combined.drop_duplicates(subset=["log_id"], keep="first")
        return combined

    def _prepare_feature_frame(self, frame: pd.DataFrame, medians: dict[str, float] | None = None) -> pd.DataFrame:
        prepared = frame.copy()
        for feature_name in self.feature_names:
            if feature_name not in prepared.columns:
                prepared[feature_name] = 0.0
            prepared[feature_name] = pd.to_numeric(prepared[feature_name], errors="coerce")

        numeric = prepared[self.feature_names].replace([np.inf, -np.inf], np.nan)
        fill_values = medians or numeric.median(numeric_only=True).to_dict()
        numeric = numeric.fillna(fill_values).fillna(0.0)
        return numeric

    def _predict_proba(self, values: np.ndarray) -> np.ndarray:
        frame = pd.DataFrame(values, columns=self.feature_names)
        prepared = self._prepare_feature_frame(frame, medians=self.feature_medians)
        return self.model.predict_proba(prepared)

    def _predict_anomaly_probability(self, feature_row: pd.Series | np.ndarray) -> float:
        values = np.asarray(feature_row[self.feature_names] if isinstance(feature_row, pd.Series) else feature_row, dtype=float)
        probabilities = self._predict_proba(np.array([values]))[0]
        class_labels = list(getattr(self.model, "classes_", [0, 1]))
        anomaly_index = class_labels.index(1) if 1 in class_labels else len(class_labels) - 1
        return float(probabilities[anomaly_index])

    def _predicted_label(self, anomaly_probability: float) -> str:
        return "anomaly" if anomaly_probability >= 0.5 else "normal"

    def _row_from_reference_dataset(self, log_id: int) -> pd.Series | None:
        if "log_id" not in self.reference_frame.columns:
            return None
        log_ids = pd.to_numeric(self.reference_frame["log_id"], errors="coerce")
        matches = self.reference_features.loc[log_ids == log_id]
        if matches.empty:
            return None
        return matches.iloc[-1]

    def _row_from_live_context(self, log_id: int, raw_log_context: pd.DataFrame) -> pd.Series | None:
        if raw_log_context.empty or "id" not in raw_log_context.columns:
            return None

        engineer = FeatureEngineer(connect_db=False)
        sink = StringIO()
        with redirect_stdout(sink):
            feature_frame, prepared_logs = engineer.extract_features(raw_log_context.copy())

        log_ids = pd.to_numeric(prepared_logs["id"], errors="coerce")
        matches = feature_frame.loc[log_ids == log_id]
        if matches.empty:
            return None
        prepared = self._prepare_feature_frame(matches.iloc[[-1]], medians=self.feature_medians)
        return prepared.iloc[0]

    def _resolve_feature_row(
        self,
        log_id: int,
        raw_log_context: pd.DataFrame | None = None,
    ) -> tuple[pd.Series, str]:
        feature_row = self._row_from_reference_dataset(log_id)
        feature_source = "dataset"
        if feature_row is None and raw_log_context is not None:
            feature_row = self._row_from_live_context(log_id, raw_log_context)
            feature_source = "live_context"

        if feature_row is None:
            raise LookupError(f"No feature row is available for log_id={log_id}.")
        return feature_row, feature_source

    def _resolve_label_column(self, frame: pd.DataFrame) -> str | None:
        for candidate in ("reviewed_label", "label"):
            if candidate in frame.columns and frame[candidate].notna().any():
                return candidate
        return None

    def _reference_candidate_rows(self, desired_label: int) -> tuple[pd.DataFrame, str]:
        if self.reference_label_column is not None:
            actual_labels = pd.to_numeric(self.reference_frame[self.reference_label_column], errors="coerce")
            actual_candidates = self.reference_features.loc[actual_labels == desired_label]
            if not actual_candidates.empty:
                predictions = actual_candidates.apply(
                    lambda row: 1 if self._predict_anomaly_probability(row) >= 0.5 else 0,
                    axis=1,
                )
                aligned = actual_candidates.loc[predictions == desired_label]
                if not aligned.empty:
                    return aligned, "actual_label_and_model"
                return actual_candidates, "actual_label"

        predicted_labels = self.reference_features.apply(
            lambda row: 1 if self._predict_anomaly_probability(row) >= 0.5 else 0,
            axis=1,
        )
        predicted_candidates = self.reference_features.loc[predicted_labels == desired_label]
        if predicted_candidates.empty:
            raise LookupError(f"No reference examples were found for desired label={desired_label}.")
        return predicted_candidates, "model_prediction"
