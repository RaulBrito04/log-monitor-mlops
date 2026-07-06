from __future__ import annotations

from datetime import timedelta
import pickle
from types import SimpleNamespace

import pytest

import pandas as pd

from src.ml.feedback_retraining import (
    PromotionConfig,
    SplitConfig,
    build_promotion_decision,
    build_retraining_runtime_metrics_payload,
    build_reviewed_splits,
    explode_feedback_events,
    map_feedback_label_to_training_target,
)


def test_map_feedback_label_to_training_target_defers_false_negative():
    assert map_feedback_label_to_training_target("true_positive") == (1, True, None)
    assert map_feedback_label_to_training_target("false_positive") == (0, True, None)
    assert map_feedback_label_to_training_target("false_negative") == (
        None,
        False,
        "false_negative_requires_missed_event_linkage",
    )


def test_explode_feedback_events_keeps_latest_review_for_same_log():
    base_time = pd.Timestamp("2026-06-30T10:00:00Z")
    feedback_events = pd.DataFrame(
        [
            {
                "feedback_id": 1,
                "alert_id": 10,
                "feedback_label": "true_positive",
                "feedback_created_at": base_time,
                "log_ids": [101],
            },
            {
                "feedback_id": 2,
                "alert_id": 11,
                "feedback_label": "false_positive",
                "feedback_created_at": base_time + timedelta(minutes=5),
                "log_ids": [101, 102],
            },
        ]
    )

    exploded = explode_feedback_events(feedback_events)

    log_101 = exploded.loc[exploded["log_id"] == 101].iloc[0]
    assert log_101["feedback_id"] == 2
    assert log_101["feedback_label"] == "false_positive"
    assert bool(log_101["had_feedback_conflict"]) is True
    assert set(exploded["log_id"].dropna().astype(int).tolist()) == {101, 102}



def test_load_scaler_falls_back_to_matching_secondary_artifact(tmp_path):
    from src.ml.feedback_retraining import ReviewedFeedbackBuilder

    models_dir = tmp_path / "models"
    data_dir = tmp_path / "data"
    models_dir.mkdir()
    data_dir.mkdir()

    with (models_dir / "scaler.pkl").open("wb") as handle:
        pickle.dump(SimpleNamespace(feature_names_in_=["only_old_feature"]), handle)
    with (models_dir / "iforest_scaler.pkl").open("wb") as handle:
        pickle.dump(SimpleNamespace(feature_names_in_=["f1", "f2"]), handle)

    builder = ReviewedFeedbackBuilder(data_dir=str(data_dir), models_dir=str(models_dir))
    scaler = builder._load_scaler("supervised", ["f1", "f2"])

    assert list(scaler.feature_names_in_) == ["f1", "f2"]


def test_build_reviewed_splits_uses_latest_rows_for_temporal_holdout():
    timestamps = pd.date_range("2026-06-01", periods=40, freq="h", tz="UTC")
    dataset = pd.DataFrame(
        {
            "feedback_id": range(1, 41),
            "timestamp": timestamps,
            "reviewed_label": [0, 1] * 20,
        }
    )

    splits = build_reviewed_splits(
        dataset,
        split_config=SplitConfig(
            reviewed_holdout_frac=0.25,
            temporal_holdout_frac=0.20,
            min_reviewed_holdout_rows=6,
            min_temporal_holdout_rows=6,
        ),
    )

    assert set(splits.keys()) == {"train", "reviewed_holdout", "temporal_holdout"}
    assert splits["temporal_holdout"]["timestamp"].min() > splits["train"]["timestamp"].max()
    assert splits["temporal_holdout"]["timestamp"].min() > splits["reviewed_holdout"]["timestamp"].min()
    assert len(splits["train"]) + len(splits["reviewed_holdout"]) + len(splits["temporal_holdout"]) == len(dataset)



def test_build_retraining_runtime_metrics_payload_exposes_candidate_deltas():
    report = {"status": "candidate_built", "generated_at": "2026-06-30T16:00:00Z"}
    dataset_summary = {
        "eligible_feedback_events": 40,
        "ready_for_training_log_samples": 80,
        "new_feedback_events_since_current_model": 12,
        "new_training_rows_since_current_model": 24,
    }
    baseline = {
        "reviewed_holdout": {"f1_score": 0.61},
        "temporal_holdout": {"f1_score": 0.58, "precision": 0.64},
    }
    candidate = {
        "reviewed_holdout": {"f1_score": 0.66},
        "temporal_holdout": {"f1_score": 0.57, "precision": 0.62},
    }
    promotion = {"promotable": False}

    payload = build_retraining_runtime_metrics_payload(report, dataset_summary, baseline, candidate, promotion)

    assert payload["retraining_status"] == "candidate_built"
    assert payload["retraining_baseline_available"] == 1
    assert payload["retraining_candidate_available"] == 1
    assert payload["retraining_promotable"] == 0
    assert payload["retraining_feedback_events"] == 40
    assert payload["retraining_ready_log_samples"] == 80
    assert payload["retraining_reviewed_f1_delta"] == pytest.approx(0.05)
    assert payload["retraining_temporal_f1_delta"] == pytest.approx(-0.01)
    assert payload["retraining_temporal_precision_delta"] == pytest.approx(-0.02)


def test_build_promotion_decision_passes_when_all_checks_hold():
    dataset_summary = {
        "eligible_feedback_events": 40,
        "ready_for_training_log_samples": 80,
        "new_feedback_events_since_current_model": 20,
    }
    split_summary = {
        "train": {"rows": 50, "positives": 12, "negatives": 38, "feedback_events": 25},
        "reviewed_holdout": {"rows": 15, "positives": 5, "negatives": 10, "feedback_events": 10},
        "temporal_holdout": {"rows": 15, "positives": 4, "negatives": 11, "feedback_events": 9},
    }
    baseline = {
        "reviewed_holdout": {"f1_score": 0.60, "precision": 0.62},
        "temporal_holdout": {"f1_score": 0.58, "precision": 0.64},
    }
    candidate = {
        "reviewed_holdout": {"f1_score": 0.64, "precision": 0.66},
        "temporal_holdout": {"f1_score": 0.58, "precision": 0.63},
    }

    decision = build_promotion_decision(dataset_summary, split_summary, baseline, candidate, PromotionConfig())

    assert decision["promotable"] is True
    assert decision["failed_checks"] == []



def test_build_promotion_decision_blocks_without_baseline():
    dataset_summary = {
        "eligible_feedback_events": 40,
        "ready_for_training_log_samples": 80,
        "new_feedback_events_since_current_model": 20,
    }
    split_summary = {
        "train": {"rows": 50, "positives": 12, "negatives": 38, "feedback_events": 25},
        "reviewed_holdout": {"rows": 15, "positives": 5, "negatives": 10, "feedback_events": 10},
        "temporal_holdout": {"rows": 15, "positives": 4, "negatives": 11, "feedback_events": 9},
    }
    candidate = {
        "reviewed_holdout": {"f1_score": 0.70, "precision": 0.75},
        "temporal_holdout": {"f1_score": 0.68, "precision": 0.70},
    }

    decision = build_promotion_decision(dataset_summary, split_summary, None, candidate, PromotionConfig())

    assert decision["promotable"] is False
    assert "baseline_available" in decision["failed_checks"]
