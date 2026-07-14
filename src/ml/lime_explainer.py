from __future__ import annotations

from typing import Any

try:
    from lime.lime_tabular import LimeTabularExplainer
except ImportError:
    LimeTabularExplainer = None

from src.ml.reference_explainer import RandomForestReferenceExplainerBase, _safe_load_pickle


class LimeUnavailableError(RuntimeError):
    """Raised when the optional LIME dependency is unavailable."""


class LimeAlertExplainer(RandomForestReferenceExplainerBase):
    """Local alert explanation using LIME over the deployed Random Forest model."""

    def __init__(
        self,
        model_path: str = 'models/random_forest_latest.pkl',
        features_path: str = 'data/selected_features.txt',
        dataset_paths: list[str] | None = None,
        allowed_model_dir: str = 'models',
        background_sample_size: int = 750,
        random_state: int = 42,
    ) -> None:
        if LimeTabularExplainer is None:
            raise LimeUnavailableError('LIME is not installed. Add the dependency from requirements.txt and rebuild the environment.')

        super().__init__(
            model_path=model_path,
            features_path=features_path,
            dataset_paths=dataset_paths,
            allowed_model_dir=allowed_model_dir,
        )

        background_rows = min(background_sample_size, len(self.reference_features))
        background = self.reference_features.sample(n=background_rows, random_state=random_state)
        self.explainer = LimeTabularExplainer(
            training_data=background[self.feature_names].to_numpy(dtype=float),
            feature_names=self.feature_names,
            class_names=['normal', 'anomaly'],
            mode='classification',
            discretize_continuous=True,
            random_state=random_state,
        )

    def _match_feature_name(self, rule_text: str) -> str | None:
        for feature_name in sorted(self.feature_names, key=len, reverse=True):
            if feature_name in rule_text:
                return feature_name
        return None

    def explain_log(self, log_id: int, raw_log_context=None, top_k: int = 8) -> dict[str, Any]:
        feature_row, feature_source = self._resolve_feature_row(log_id, raw_log_context=raw_log_context)
        values = feature_row[self.feature_names].to_numpy(dtype=float)
        probabilities = self._predict_proba(values.reshape(1, -1))[0]
        class_labels = list(getattr(self.model, 'classes_', [0, 1]))
        anomaly_index = class_labels.index(1) if 1 in class_labels else len(class_labels) - 1
        anomaly_probability = float(probabilities[anomaly_index])
        predicted_label = self._predicted_label(anomaly_probability)

        explanation = self.explainer.explain_instance(
            data_row=values,
            predict_fn=self._predict_proba,
            num_features=min(top_k, len(self.feature_names)),
            top_labels=min(2, len(class_labels)),
        )

        top_features: list[dict[str, Any]] = []
        for rank, (rule, weight) in enumerate(explanation.as_list(label=anomaly_index), start=1):
            feature_name = self._match_feature_name(rule)
            feature_value = None
            if feature_name is not None:
                feature_value = float(feature_row[feature_name])
            top_features.append(
                {
                    'rank': rank,
                    'feature': feature_name or rule,
                    'rule': rule,
                    'weight': float(weight),
                    'direction': 'pushes_anomaly' if weight >= 0 else 'pushes_normal',
                    'value': feature_value,
                }
            )

        return {
            'model_family': 'random_forest',
            'log_id': int(log_id),
            'feature_source': feature_source,
            'anomaly_probability': anomaly_probability,
            'predicted_label': predicted_label,
            'top_features': top_features,
        }
