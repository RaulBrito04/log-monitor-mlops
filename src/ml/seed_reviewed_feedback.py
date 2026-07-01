#!/usr/bin/env python3
"""Bootstrap reviewed feedback from controlled scenarios and benign healthcheck alerts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import math
import os

import pandas as pd
import psycopg2

ATTACK_SCENARIOS = ("sql_injection", "brute_force", "scanning", "rate_abuse", "offhours")
HEALTHCHECK_REASON = (
    "Bootstrap false_positive from localhost /health GET 200 requests with python-requests user agent; "
    "treated as benign monitoring traffic."
)
ATTACK_REASON_PREFIX = "Bootstrap true_positive from controlled attack scenario(s): "
DEFAULT_USER_ID = "system_bootstrap_review"
SUMMARY_PATH = Path("data/reviewed_feedback_seed_summary.json")


@dataclass(frozen=True)
class SeedConfig:
    negative_ratio: float = 2.0
    max_positive_alerts: int | None = None
    max_negative_alerts: int | None = None
    user_id: str = DEFAULT_USER_ID
    dry_run: bool = False


def db_connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "logmonitor"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme"),
    )


def sample_evenly(frame: pd.DataFrame, limit: int | None) -> pd.DataFrame:
    if frame.empty:
        return frame.reset_index(drop=True)
    if limit is None:
        return frame.reset_index(drop=True)
    if limit <= 0:
        return frame.head(0).reset_index(drop=True)
    if len(frame) <= limit:
        return frame.reset_index(drop=True)
    positions = sorted({int(round(idx)) for idx in pd.Series(range(limit)).map(lambda i: i * (len(frame) - 1) / max(limit - 1, 1))})
    sampled = frame.iloc[positions].copy()
    while len(sampled) < limit:
        missing = limit - len(sampled)
        extra = frame.drop(sampled.index, errors="ignore").head(missing)
        sampled = pd.concat([sampled, extra])
    return sampled.head(limit).reset_index(drop=True)


def load_positive_candidates(conn) -> pd.DataFrame:
    sql = """
    SELECT
        a.id AS alert_id,
        a.alert_type,
        a.severity,
        a.source,
        a.confidence,
        COALESCE(a.ip::text, 'NULL') AS ip,
        a.timestamp,
        ARRAY_AGG(DISTINCT COALESCE(r.data->>'scenario', 'NULL')) AS scenarios,
        COUNT(DISTINCT log_id) AS linked_logs
    FROM alerts a
    JOIN LATERAL UNNEST(a.log_ids) AS log_id ON TRUE
    JOIN raw_logs r ON r.id = log_id
    LEFT JOIN feedback f ON f.alert_id = a.id
    WHERE f.id IS NULL
    GROUP BY a.id, a.alert_type, a.severity, a.source, a.confidence, a.ip, a.timestamp
    HAVING BOOL_AND(COALESCE(r.data->>'scenario', 'NULL') IN ('sql_injection', 'brute_force', 'scanning', 'rate_abuse', 'offhours'))
    ORDER BY a.timestamp ASC, a.id ASC
    """
    return pd.read_sql(sql, conn)


def load_negative_candidates(conn) -> pd.DataFrame:
    sql = """
    SELECT
        a.id AS alert_id,
        a.alert_type,
        a.severity,
        a.source,
        a.confidence,
        COALESCE(a.ip::text, 'NULL') AS ip,
        a.timestamp,
        COUNT(DISTINCT log_id) AS linked_logs
    FROM alerts a
    JOIN LATERAL UNNEST(a.log_ids) AS log_id ON TRUE
    JOIN raw_logs r ON r.id = log_id
    LEFT JOIN feedback f ON f.alert_id = a.id
    WHERE f.id IS NULL
      AND a.alert_type = 'suspicious_user_agent'
    GROUP BY a.id, a.alert_type, a.severity, a.source, a.confidence, a.ip, a.timestamp
    HAVING BOOL_AND(COALESCE(a.ip::text, 'NULL') = '127.0.0.1/32')
       AND BOOL_AND(COALESCE(r.endpoint, '') = '/health')
       AND BOOL_AND(COALESCE(r.method, '') = 'GET')
       AND BOOL_AND(COALESCE(r.status, 0) = 200)
       AND BOOL_AND(COALESCE(r.user_agent, '') LIKE 'python-requests/%')
       AND BOOL_AND(COALESCE(r.data->>'scenario', 'NULL') = 'NULL')
    ORDER BY a.timestamp ASC, a.id ASC
    """
    return pd.read_sql(sql, conn)


def build_seed_rows(config: SeedConfig, positive_candidates: pd.DataFrame, negative_candidates: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    positives = sample_evenly(positive_candidates, config.max_positive_alerts)
    negative_limit = math.ceil(len(positives) * config.negative_ratio)
    if config.max_negative_alerts is not None:
        negative_limit = min(negative_limit, config.max_negative_alerts)
    negatives = sample_evenly(negative_candidates, negative_limit)

    positive_rows = []
    for record in positives.to_dict(orient="records"):
        scenarios = record.get("scenarios") or []
        if isinstance(scenarios, str):
            scenario_text = scenarios
        else:
            scenario_text = ", ".join(str(item) for item in scenarios)
        positive_rows.append(
            {
                "alert_id": int(record["alert_id"]),
                "user_id": config.user_id,
                "label": "true_positive",
                "reason": ATTACK_REASON_PREFIX + scenario_text,
                "seed_type": "controlled_attack",
            }
        )

    negative_rows = [
        {
            "alert_id": int(record["alert_id"]),
            "user_id": config.user_id,
            "label": "false_positive",
            "reason": HEALTHCHECK_REASON,
            "seed_type": "benign_healthcheck",
        }
        for record in negatives.to_dict(orient="records")
    ]

    seeded = pd.DataFrame(positive_rows + negative_rows)
    stats = {
        "positive_candidates": int(len(positive_candidates)),
        "negative_candidates": int(len(negative_candidates)),
        "selected_true_positive_alerts": int(len(positives)),
        "selected_false_positive_alerts": int(len(negatives)),
        "seed_rows": int(len(seeded)),
    }
    return seeded, stats


def persist_seed_feedback(conn, seed_rows: pd.DataFrame) -> int:
    if seed_rows.empty:
        return 0
    inserted = 0
    with conn.cursor() as cursor:
        for record in seed_rows.to_dict(orient="records"):
            cursor.execute(
                """
                INSERT INTO feedback (alert_id, user_id, label, reason)
                SELECT %s, %s, %s, %s
                WHERE NOT EXISTS (SELECT 1 FROM feedback WHERE alert_id = %s)
                """,
                (
                    int(record["alert_id"]),
                    record["user_id"],
                    record["label"],
                    record["reason"],
                    int(record["alert_id"]),
                ),
            )
            inserted += cursor.rowcount
    conn.commit()
    return inserted


def write_summary(summary: dict) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def run_seed(config: SeedConfig) -> dict:
    with db_connect() as conn:
        positive_candidates = load_positive_candidates(conn)
        negative_candidates = load_negative_candidates(conn)
        seed_rows, stats = build_seed_rows(config, positive_candidates, negative_candidates)
        inserted = 0 if config.dry_run else persist_seed_feedback(conn, seed_rows)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config),
        **stats,
        "inserted_feedback_rows": int(inserted),
        "selected_alert_ids_preview": seed_rows["alert_id"].head(25).astype(int).tolist() if not seed_rows.empty else [],
        "notes": [
            "true_positive seeds come only from alerts linked exclusively to controlled attack scenarios",
            "false_positive seeds come only from localhost /health python-requests alerts treated as benign monitoring traffic",
            "this bootstrap is for S18 retraining demonstration and remains distinct from future human-reviewed production feedback",
        ],
    }
    write_summary(summary)
    return summary


def parse_args() -> SeedConfig:
    parser = argparse.ArgumentParser(description="Bootstrap reviewed feedback from known-safe alert patterns")
    parser.add_argument("--negative-ratio", type=float, default=2.0, help="False-positive samples per true-positive seed")
    parser.add_argument("--max-positive-alerts", type=int, default=None)
    parser.add_argument("--max-negative-alerts", type=int, default=None)
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return SeedConfig(
        negative_ratio=args.negative_ratio,
        max_positive_alerts=args.max_positive_alerts,
        max_negative_alerts=args.max_negative_alerts,
        user_id=args.user_id,
        dry_run=args.dry_run,
    )


def main() -> dict:
    config = parse_args()
    summary = run_seed(config)
    print("=" * 72)
    print("REVIEWED FEEDBACK BOOTSTRAP")
    print("=" * 72)
    for key in [
        "positive_candidates",
        "negative_candidates",
        "selected_true_positive_alerts",
        "selected_false_positive_alerts",
        "seed_rows",
        "inserted_feedback_rows",
    ]:
        print(f"{key}: {summary[key]}")
    print(f"summary: {SUMMARY_PATH}")
    return summary


if __name__ == "__main__":
    main()
