from __future__ import annotations

import pickle

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from src.ml.counterfactual_explainer import CounterfactualAlertExplainer


def test_counterfactual_alert_explainer_returns_changed_features(tmp_path):
    features_path = tmp_path / 'selected_features.txt'
    features_path.write_text('response_time_ms\nstatus_code\n', encoding='utf-8')

    dataset_path = tmp_path / 'ml_dataset.csv'
    pd.DataFrame(
        [
            {'log_id': 1001, 'response_time_ms': 20.0, 'status_code': 200, 'label': 0},
            {'log_id': 1002, 'response_time_ms': 260.0, 'status_code': 500, 'label': 1},
            {'log_id': 1003, 'response_time_ms': 35.0, 'status_code': 200, 'label': 0},
            {'log_id': 1004, 'response_time_ms': 240.0, 'status_code': 404, 'label': 1},
        ]
    ).to_csv(dataset_path, index=False)

    model = RandomForestClassifier(n_estimators=20, random_state=42)
    train_x = pd.DataFrame(
        [
            {'response_time_ms': 20.0, 'status_code': 200},
            {'response_time_ms': 35.0, 'status_code': 200},
            {'response_time_ms': 260.0, 'status_code': 500},
            {'response_time_ms': 240.0, 'status_code': 404},
        ]
    )
    train_y = [0, 0, 1, 1]
    model.fit(train_x, train_y)

    model_path = tmp_path / 'random_forest_latest.pkl'
    with open(model_path, 'wb') as handle:
        pickle.dump(model, handle)

    explainer = CounterfactualAlertExplainer(
        model_path=model_path,
        features_path=features_path,
        dataset_paths=[dataset_path],
        allowed_model_dir=tmp_path,
    )

    explanation = explainer.explain_log(1002)

    assert explanation['model_family'] == 'random_forest'
    assert explanation['feature_source'] == 'dataset'
    assert explanation['log_id'] == 1002
    assert explanation['predicted_label'] == 'anomaly'
    assert explanation['counterfactual_label'] == 'normal'
    assert explanation['counterfactual_found'] is True
    assert explanation['changed_feature_count'] >= 1
    assert explanation['counterfactual_anomaly_probability'] < explanation['anomaly_probability']
    assert explanation['changed_features'][0]['feature'] in {'response_time_ms', 'status_code'}
