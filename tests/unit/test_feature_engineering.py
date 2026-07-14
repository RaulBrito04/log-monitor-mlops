from __future__ import annotations

from datetime import timedelta

import pandas as pd

from src.ml.feature_engineering import FeatureEngineer


def _engineer() -> FeatureEngineer:
    return FeatureEngineer.__new__(FeatureEngineer)


def _build_logs(count: int = 3, *, same_ip: bool = False) -> pd.DataFrame:
    base_ts = pd.Timestamp('2026-07-07T12:00:00Z')
    rows = []
    for idx in range(count):
        rows.append(
            {
                'id': idx + 1,
                'timestamp': base_ts + timedelta(seconds=idx),
                'ip': '10.0.0.1' if same_ip else f'10.0.0.{(idx % 2) + 1}',
                'method': 'GET' if idx % 2 == 0 else 'POST',
                'endpoint': '/login?next=/admin' if idx % 2 == 0 else '/health',
                'status': 401 if idx % 2 == 0 else 200,
                'response_time_ms': 100 + idx,
                'user_agent': 'pytest-agent',
                'data': {'scenario': 'normal'},
            }
        )
    return pd.DataFrame(rows)


class TestFeatureEngineering:
    def test_extract_features_returns_expected_columns(self):
        raw_logs = _build_logs(4)

        features, prepared_logs = FeatureEngineer.extract_features(_engineer(), raw_logs)

        expected_columns = {
            'status_code',
            'response_time_ms',
            'requests_per_ip_5min',
            'unique_endpoints_5min',
            'failed_requests_ratio_5min',
            'avg_response_time_5min',
            'max_response_time_5min',
            'request_rate_5min',
            'hour_of_day',
            'day_of_week',
            'is_night',
            'endpoint_length',
            'has_query_params',
            'query_param_count',
            'endpoint_entropy',
            'time_since_last_request',
            'requests_per_minute',
            'response_time_log',
            'response_time_zscore_global',
            'response_time_zscore_endpoint',
            'requests_vs_ip_baseline_ratio',
            'endpoint_rarity',
            'method_endpoint_rarity',
            'error_burst_score',
            'hour_deviation_from_endpoint_pattern',
        }

        assert len(features) == len(raw_logs)
        assert expected_columns.issubset(set(features.columns))
        assert prepared_logs['timestamp'].dt.tz is not None
        assert features.loc[0, 'endpoint_entropy'] == features.loc[2, 'endpoint_entropy']

    def test_requests_per_minute_caps_at_sixty(self):
        raw_logs = _build_logs(70, same_ip=True)

        features, _ = FeatureEngineer.extract_features(_engineer(), raw_logs)

        assert features['requests_per_minute'].iloc[0] == 1.0
        assert features['requests_per_minute'].iloc[59] == 60.0
        assert features['requests_per_minute'].iloc[-1] == 60.0
        assert features['requests_per_minute'].max() == 60.0
        assert features['time_since_last_request'].iloc[1] == 1.0
