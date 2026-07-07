from __future__ import annotations

import pandas as pd
import pytest

from src.ml.realtime_hybrid import RealtimeHybridProcessor


@pytest.fixture
def processor_factory(mocker):
    mocker.patch("src.ml.realtime_hybrid.HybridPipeline", return_value=mocker.Mock())
    mocker.patch("src.ml.realtime_hybrid.FeatureEngineer", return_value=mocker.Mock())
    mocker.patch.object(RealtimeHybridProcessor, "_get_last_processed_id", return_value=42)

    def _build(**kwargs):
        return RealtimeHybridProcessor(**kwargs)

    return _build


class TestRealtimeHybridConfig:
    def test_reads_runtime_knobs_from_env(self, processor_factory, monkeypatch):
        monkeypatch.setenv("HYBRID_POLL_INTERVAL_SEC", "5")
        monkeypatch.setenv("HYBRID_FETCH_LIMIT", "750")

        processor = processor_factory()

        assert processor.poll_interval == 5.0
        assert processor.fetch_limit == 750
        assert processor.configured_ceiling_logs_per_second() == 150.0

    def test_sleep_until_next_cycle_waits_only_remaining_time(self, processor_factory, mocker):
        sleep = mocker.patch("src.ml.realtime_hybrid.time.sleep")
        processor = processor_factory(poll_interval_sec=5.0, fetch_limit=500)

        waited = processor._sleep_until_next_cycle(1.25)

        assert waited == pytest.approx(3.75)
        sleep.assert_called_once_with(pytest.approx(3.75))

    def test_sleep_until_next_cycle_skips_wait_when_cycle_is_slower(self, processor_factory, mocker):
        sleep = mocker.patch("src.ml.realtime_hybrid.time.sleep")
        processor = processor_factory(poll_interval_sec=5.0, fetch_limit=500)

        waited = processor._sleep_until_next_cycle(7.0)

        assert waited == 0.0
        sleep.assert_not_called()

    def test_fetch_new_logs_uses_configured_fetch_limit_and_reuses_connection(self, processor_factory, mocker):
        cursor = mocker.Mock()
        cursor.fetchall.side_effect = [
            [(43, "2026-07-07T10:00:00+00:00", "10.0.0.1", "GET", "/health", 200, 12.0, "ua")],
            [],
        ]
        conn = mocker.Mock()
        conn.closed = 0
        conn.cursor.return_value = cursor
        connect = mocker.patch("src.ml.realtime_hybrid.psycopg2.connect", return_value=conn)
        processor = processor_factory(poll_interval_sec=5.0, fetch_limit=750)

        df1, _ = processor.fetch_new_logs()
        df2, _ = processor.fetch_new_logs()

        assert list(df1.columns) == ["id", "timestamp", "ip", "method", "endpoint", "status", "response_time_ms", "user_agent"]
        assert len(df1) == 1
        assert df2.empty
        assert cursor.execute.call_args_list[0].args[1] == (42, 750)
        assert cursor.execute.call_args_list[1].args[1] == (42, 750)
        connect.assert_called_once()

    def test_close_closes_fetch_connection(self, processor_factory, mocker):
        conn = mocker.Mock()
        conn.closed = 0
        processor = processor_factory(poll_interval_sec=5.0, fetch_limit=500)
        processor.fetch_conn = conn

        processor.close()

        conn.close.assert_called_once()
        assert processor.fetch_conn is None

    def test_process_logs_uses_batch_evaluation(self, processor_factory):
        processor = processor_factory(poll_interval_sec=5.0, fetch_limit=500)
        raw_df = pd.DataFrame({"id": [100, 101]})
        features_df = pd.DataFrame(
            [
                {"status_code": 200.0, "response_time_ms": 20.0, "endpoint_entropy": 0.2},
                {"status_code": 401.0, "response_time_ms": 50.0, "endpoint_entropy": 1.1},
            ]
        )
        processor.feat_eng.extract_features.return_value = (features_df, {})
        processor.pipeline.evaluate_batch.return_value = pd.DataFrame(
            [
                {
                    "log_id": 100,
                    "severity": "NORMAL",
                    "final_score": 0.20,
                    "triggered_rules": [],
                    "is_anomaly": False,
                },
                {
                    "log_id": 101,
                    "severity": "HIGH",
                    "final_score": 0.72,
                    "triggered_rules": ["brute_force"],
                    "is_anomaly": True,
                },
            ]
        )

        summary = processor.process_logs(raw_df)

        processor.pipeline.evaluate_batch.assert_called_once()
        batch_df = processor.pipeline.evaluate_batch.call_args.args[0]
        assert list(batch_df["log_id"]) == [100, 101]
        assert summary["logs_processed"] == 2
        assert summary["anomalies_found"] == 1
        assert processor.last_processed_id == 101
