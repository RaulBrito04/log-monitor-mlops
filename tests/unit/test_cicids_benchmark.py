from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.ml.cicids_benchmark import (
    build_temporal_splits,
    normalize_cicids_frame,
    resolve_csv_paths,
    run_benchmark,
    select_numeric_features,
)


def build_fixture_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    base = pd.Timestamp("2026-01-01T00:00:00Z")
    attack_indexes = {14, 16, 18, 21, 24, 27}
    for idx in range(30):
        is_attack = idx in attack_indexes
        rows.append(
            {
                " Timestamp": (base + pd.Timedelta(minutes=idx)).isoformat(),
                " Flow Duration": 5000 + idx * 9 if not is_attack else 85000 + idx * 120,
                " Total Fwd Packets": 10 + (idx % 4) if not is_attack else 180 + idx,
                " Total Backward Packets": 8 + (idx % 3) if not is_attack else 150 + idx,
                " Flow Bytes/s": 1200 + idx * 5 if not is_attack else 140000 + idx * 30,
                " Flow Packets/s": 14 + (idx % 5) if not is_attack else 900 + idx * 4,
                " Packet Length Mean": 350 + (idx % 6) if not is_attack else 1550 + idx * 2,
                " Destination Port": 80 if not is_attack else 4444,
                " Label": "BENIGN" if not is_attack else "DoS Hulk",
            }
        )
    return pd.DataFrame(rows)


def test_normalize_cicids_frame_maps_labels_and_timestamps():
    frame = build_fixture_frame().iloc[:2].copy()
    frame.loc[1, " Label"] = "DoS Slowloris"

    normalized = normalize_cicids_frame(frame)

    assert "benchmark_label" in normalized.columns
    assert normalized["benchmark_label"].tolist() == [0, 1]
    assert str(normalized["timestamp"].dtype).startswith("datetime64")


def test_resolve_csv_paths_accepts_directory(tmp_path):
    sample = tmp_path / "part1.csv"
    build_fixture_frame().iloc[:4].to_csv(sample, index=False)

    paths = resolve_csv_paths([tmp_path])

    assert paths == [sample.resolve()]


def test_build_temporal_splits_preserves_order():
    normalized = normalize_cicids_frame(build_fixture_frame())
    features, feature_cols = select_numeric_features(normalized)
    dataset = normalized[["label", "benchmark_label", "timestamp", "source_file", "event_order"]].join(features)

    splits = build_temporal_splits(dataset)

    assert splits["train"]["timestamp"].max() < splits["validation"]["timestamp"].min()
    assert splits["validation"]["timestamp"].max() < splits["test"]["timestamp"].min()
    assert len(feature_cols) >= 4


def test_run_benchmark_writes_report_and_markdown(tmp_path):
    csv_path = tmp_path / "cicids_sample.csv"
    build_fixture_frame().to_csv(csv_path, index=False)
    report_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    report = run_benchmark([csv_path], report_path=report_path, markdown_path=markdown_path, random_state=7)

    assert report_path.exists()
    assert markdown_path.exists()
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["dataset"]["total_rows"] == 30
    assert persisted["models"]["ensemble"]["test"]["f1_score"] >= 0.5
    assert persisted["models"]["iforest"]["test"]["roc_auc"] >= 0.5
