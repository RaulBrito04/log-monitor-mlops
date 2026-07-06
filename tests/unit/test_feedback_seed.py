from __future__ import annotations

import pandas as pd

from src.ml.seed_reviewed_feedback import HEALTHCHECK_REASON, SeedConfig, build_seed_rows, sample_evenly


def test_sample_evenly_preserves_requested_limit_and_endpoints():
    frame = pd.DataFrame({"alert_id": list(range(10))})

    sampled = sample_evenly(frame, 4)

    assert len(sampled) == 4
    assert sampled["alert_id"].tolist()[0] == 0
    assert sampled["alert_id"].tolist()[-1] == 9


def test_sample_evenly_with_zero_limit_returns_empty_frame():
    frame = pd.DataFrame({"alert_id": list(range(5))})

    sampled = sample_evenly(frame, 0)

    assert sampled.empty


def test_build_seed_rows_labels_controlled_attack_and_benign_healthcheck():
    positive_candidates = pd.DataFrame(
        [
            {"alert_id": 10, "scenarios": ["sql_injection"]},
            {"alert_id": 11, "scenarios": ["brute_force", "scanning"]},
        ]
    )
    negative_candidates = pd.DataFrame(
        [
            {"alert_id": 20},
            {"alert_id": 21},
            {"alert_id": 22},
            {"alert_id": 23},
        ]
    )

    seeded, stats = build_seed_rows(
        SeedConfig(negative_ratio=1.5, user_id="reviewer_bootstrap"),
        positive_candidates,
        negative_candidates,
    )

    assert stats["selected_true_positive_alerts"] == 2
    assert stats["selected_false_positive_alerts"] == 3
    assert stats["seed_rows"] == 5

    positives = seeded.loc[seeded["label"] == "true_positive"]
    negatives = seeded.loc[seeded["label"] == "false_positive"]

    assert positives["user_id"].unique().tolist() == ["reviewer_bootstrap"]
    assert positives.iloc[0]["reason"].startswith("Bootstrap true_positive from controlled attack scenario(s):")
    assert negatives["reason"].unique().tolist() == [HEALTHCHECK_REASON]
    assert set(negatives["seed_type"].tolist()) == {"benign_healthcheck"}
