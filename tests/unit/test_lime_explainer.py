from __future__ import annotations

import pickle

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from src.ml import lime_explainer as lime_module


class FakeExplanation:
    def as_list(self, label=1):
        return [
            ('response_time_ms > 100.0', 0.42),
            ('status_code <= 400.0', -0.11),
        ]


class FakeLimeTabularExplainer:
    def __init__(self, training_data, feature_names, class_names, mode, discretize_continuous, random_state):
        self.training_data = training_data
        self.feature_names = feature_names
        self.class_names = class_names
        self.mode = mode
        self.discretize_continuous = discretize_continuous
        self.random_state = random_state

    def explain_instance(self, data_row, predict_fn, num_features, top_labels):
        probabilities = predict_fn(pd.DataFrame([data_row], columns=self.feature_names).to_numpy())
        assert probabilities.shape[1] == 2
        assert num_features == 2
        assert top_labels == 2
        return FakeExplanation()


def test_lime_alert_explainer_uses_reference_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(lime_module, 'LimeTabularExplainer', FakeLimeTabularExplainer)

    features_path = tmp_path / 'selected_features.txt'
    features_path.write_text('response_time_ms\nstatus_code\n', encoding='utf-8')

    dataset_path = tmp_path / 'ml_dataset.csv'
    pd.DataFrame(
        [
            {'log_id': 1001, 'response_time_ms': 20.0, 'status_code': 200, 'label': 0},
            {'log_id': 1002, 'response_time_ms': 250.0, 'status_code': 500, 'label': 1},
            {'log_id': 1003, 'response_time_ms': 120.0, 'status_code': 404, 'label': 1},
        ]
    ).to_csv(dataset_path, index=False)

    model = RandomForestClassifier(n_estimators=10, random_state=42)
    train_x = pd.DataFrame(
        [
            {'response_time_ms': 20.0, 'status_code': 200},
            {'response_time_ms': 250.0, 'status_code': 500},
            {'response_time_ms': 120.0, 'status_code': 404},
            {'response_time_ms': 35.0, 'status_code': 200},
        ]
    )
    train_y = [0, 1, 1, 0]
    model.fit(train_x, train_y)

    model_path = tmp_path / 'random_forest_latest.pkl'
    with open(model_path, 'wb') as handle:
        pickle.dump(model, handle)

    explainer = lime_module.LimeAlertExplainer(
        model_path=model_path,
        features_path=features_path,
        dataset_paths=[dataset_path],
        allowed_model_dir=tmp_path,
        background_sample_size=3,
    )

    explanation = explainer.explain_log(1002, top_k=2)

    assert explanation['model_family'] == 'random_forest'
    assert explanation['feature_source'] == 'dataset'
    assert explanation['log_id'] == 1002
    assert explanation['predicted_label'] in {'normal', 'anomaly'}
    assert len(explanation['top_features']) == 2
    assert explanation['top_features'][0]['feature'] == 'response_time_ms'

