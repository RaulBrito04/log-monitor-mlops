from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.ml.reference_explainer import RandomForestReferenceExplainerBase


class CounterfactualAlertExplainer(RandomForestReferenceExplainerBase):
    """Heuristic local counterfactuals grounded in nearest opposite-class reference examples."""

    def __init__(
        self,
        model_path: str = 'models/random_forest_latest.pkl',
        features_path: str = 'data/selected_features.txt',
        dataset_paths: list[str] | None = None,
        allowed_model_dir: str = 'models',
    ) -> None:
        super().__init__(
            model_path=model_path,
            features_path=features_path,
            dataset_paths=dataset_paths,
            allowed_model_dir=allowed_model_dir,
        )
        importances = getattr(self.model, 'feature_importances_', None)
        if importances is None or len(importances) != len(self.feature_names):
            importances = np.ones(len(self.feature_names), dtype=float)
        self.feature_importances = {
            feature_name: float(importance)
            for feature_name, importance in zip(self.feature_names, importances, strict=False)
        }

    def _nearest_opposite_example(self, feature_row: pd.Series, desired_label: int) -> tuple[pd.Series, float, str]:
        candidates, label_source = self._reference_candidate_rows(desired_label)
        if candidates.empty:
            raise LookupError(f'No reference examples were found for desired label={desired_label}.')

        scale = pd.Series(self.feature_stds, index=self.feature_names).replace(0.0, 1.0).fillna(1.0)
        normalized = ((candidates[self.feature_names] - feature_row[self.feature_names]) / scale).abs()
        distances = normalized.sum(axis=1)
        nearest_index = distances.idxmin()
        return candidates.loc[nearest_index], float(distances.loc[nearest_index]), label_source

    def _ordered_feature_changes(self, feature_row: pd.Series, reference_row: pd.Series) -> list[str]:
        scale = pd.Series(self.feature_stds, index=self.feature_names).replace(0.0, 1.0).fillna(1.0)
        weighted_differences: list[tuple[float, str]] = []
        for feature_name in self.feature_names:
            before_value = float(feature_row[feature_name])
            after_value = float(reference_row[feature_name])
            if np.isclose(before_value, after_value, equal_nan=True):
                continue
            normalized_delta = abs((after_value - before_value) / float(scale[feature_name]))
            weight = normalized_delta * max(self.feature_importances.get(feature_name, 0.0), 1e-9)
            weighted_differences.append((weight, feature_name))
        weighted_differences.sort(reverse=True)
        return [feature_name for _weight, feature_name in weighted_differences]

    def explain_log(self, log_id: int, raw_log_context=None) -> dict[str, Any]:
        feature_row, feature_source = self._resolve_feature_row(log_id, raw_log_context=raw_log_context)
        original_probability = self._predict_anomaly_probability(feature_row)
        original_label = self._predicted_label(original_probability)
        desired_label_value = 0 if original_label == 'anomaly' else 1
        desired_label = 'normal' if desired_label_value == 0 else 'anomaly'

        reference_row, reference_distance, label_source = self._nearest_opposite_example(feature_row, desired_label_value)
        candidate_row = feature_row.copy()
        changed_features: list[dict[str, Any]] = []

        for rank, feature_name in enumerate(self._ordered_feature_changes(feature_row, reference_row), start=1):
            current_value = float(candidate_row[feature_name])
            target_value = float(reference_row[feature_name])
            if np.isclose(current_value, target_value, equal_nan=True):
                continue

            candidate_row[feature_name] = target_value
            updated_probability = self._predict_anomaly_probability(candidate_row)
            changed_features.append(
                {
                    'rank': rank,
                    'feature': feature_name,
                    'current_value': current_value,
                    'counterfactual_value': target_value,
                    'delta': float(target_value - current_value),
                    'feature_importance': float(self.feature_importances.get(feature_name, 0.0)),
                    'updated_anomaly_probability': updated_probability,
                }
            )
            if self._predicted_label(updated_probability) == desired_label:
                break

        counterfactual_probability = self._predict_anomaly_probability(candidate_row)
        counterfactual_found = self._predicted_label(counterfactual_probability) == desired_label

        return {
            'model_family': 'random_forest',
            'log_id': int(log_id),
            'feature_source': feature_source,
            'reference_label_source': label_source,
            'predicted_label': original_label,
            'anomaly_probability': float(original_probability),
            'counterfactual_label': desired_label,
            'counterfactual_anomaly_probability': float(counterfactual_probability),
            'counterfactual_found': bool(counterfactual_found),
            'nearest_reference_distance': float(reference_distance),
            'changed_feature_count': len(changed_features),
            'changed_features': changed_features,
        }
