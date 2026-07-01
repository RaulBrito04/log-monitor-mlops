from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from src.ml.seed_validation_feedback import build_feedback_rows, normalize_ip_text, parse_validation_results


def test_parse_validation_results_extracts_ips_and_timestamp(tmp_path):
    results = tmp_path / "validation.md"
    results.write_text(
        """# Real Logs Validation Results

- Validation started at: `2026-07-01T10:15:00Z`
- Benign source IP used: `198.51.100.77`
- Attack source IP used: `203.0.113.77`
""",
        encoding="utf-8",
    )

    context = parse_validation_results(results)

    assert context.benign_source_ip == "198.51.100.77"
    assert context.attack_source_ip == "203.0.113.77"
    assert context.validation_started_at == datetime(2026, 7, 1, 10, 15, tzinfo=timezone.utc)


def test_normalize_ip_text_adds_host_mask_when_missing():
    assert normalize_ip_text("203.0.113.77") == "203.0.113.77/32"
    assert normalize_ip_text("203.0.113.77/32") == "203.0.113.77/32"


def test_build_feedback_rows_uses_true_positive_for_attack_and_false_positive_for_benign():
    attack = pd.DataFrame(
        [
            {"alert_id": 10, "existing_feedback": 0},
            {"alert_id": 11, "existing_feedback": 1},
        ]
    )
    benign = pd.DataFrame(
        [
            {"alert_id": 20, "existing_feedback": 0},
        ]
    )

    rows = build_feedback_rows(attack, benign, user_id="validation_reviewer")

    assert rows["alert_id"].tolist() == [10, 20]
    assert rows["label"].tolist() == ["true_positive", "false_positive"]
    assert rows["user_id"].tolist() == ["validation_reviewer", "validation_reviewer"]
    assert rows["seed_type"].tolist() == ["real_log_validation_attack", "real_log_validation_benign"]
