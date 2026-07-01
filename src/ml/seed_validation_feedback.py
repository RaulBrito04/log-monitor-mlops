#!/usr/bin/env python3
"""Seed reviewed feedback from the latest controlled real-log validation run."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import re

import pandas as pd
import psycopg2

DEFAULT_RESULTS_PATH = Path("docs/REAL_LOGS_VALIDATION_RESULTS.md")
DEFAULT_SUMMARY_PATH = Path("data/validation_feedback_seed_summary.json")
DEFAULT_USER_ID = "system_validation_review"
ATTACK_REASON = "Controlled validation attack traffic confirmed during latest real-log validation run."
BENIGN_REASON = "Controlled validation benign baseline alert marked false_positive during latest real-log validation run."


@dataclass(frozen=True)
class ValidationSeedConfig:
    results_path: str = str(DEFAULT_RESULTS_PATH)
    summary_path: str = str(DEFAULT_SUMMARY_PATH)
    user_id: str = DEFAULT_USER_ID
    dry_run: bool = False


@dataclass(frozen=True)
class ValidationContext:
    validation_started_at: datetime
    benign_source_ip: str
    attack_source_ip: str


def db_connect():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "logmonitor"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "changeme"),
    )


def parse_validation_results(results_path: Path) -> ValidationContext:
    text = results_path.read_text(encoding="utf-8")

    started_match = re.search(r"Validation started at: `([^`]+)`", text)
    benign_match = re.search(r"Benign source IP used: `([^`]+)`", text)
    attack_match = re.search(r"Attack source IP used: `([^`]+)`", text)

    if not started_match or not benign_match or not attack_match:
        raise ValueError(f"Could not parse validation context from {results_path}")

    started_at = datetime.fromisoformat(started_match.group(1).replace("Z", "+00:00"))
    return ValidationContext(
        validation_started_at=started_at,
        benign_source_ip=benign_match.group(1),
        attack_source_ip=attack_match.group(1),
    )


def normalize_ip_text(source_ip: str) -> str:
    return source_ip if "/" in source_ip else f"{source_ip}/32"


def load_alert_candidates(conn, source_ip: str, lower_bound: datetime) -> pd.DataFrame:
    query = """
    SELECT
        a.id AS alert_id,
        a.alert_type,
        a.severity,
        a.source,
        a.timestamp,
        COALESCE(a.ip::text, 'NULL') AS ip,
        a.log_ids,
        COUNT(f.id) AS existing_feedback
    FROM alerts a
    LEFT JOIN feedback f ON f.alert_id = a.id
    WHERE a.ip::text = %s
      AND a.timestamp >= %s
    GROUP BY a.id, a.alert_type, a.severity, a.source, a.timestamp, a.ip, a.log_ids
    ORDER BY a.timestamp ASC, a.id ASC
    """
    return pd.read_sql(query, conn, params=(normalize_ip_text(source_ip), lower_bound))


def build_feedback_rows(
    attack_candidates: pd.DataFrame,
    benign_candidates: pd.DataFrame,
    user_id: str,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    for record in attack_candidates.to_dict(orient="records"):
        if int(record.get("existing_feedback", 0) or 0) > 0:
            continue
        rows.append(
            {
                "alert_id": int(record["alert_id"]),
                "label": "true_positive",
                "reason": ATTACK_REASON,
                "user_id": user_id,
                "seed_type": "real_log_validation_attack",
            }
        )

    for record in benign_candidates.to_dict(orient="records"):
        if int(record.get("existing_feedback", 0) or 0) > 0:
            continue
        rows.append(
            {
                "alert_id": int(record["alert_id"]),
                "label": "false_positive",
                "reason": BENIGN_REASON,
                "user_id": user_id,
                "seed_type": "real_log_validation_benign",
            }
        )

    return pd.DataFrame(rows)


def persist_feedback_rows(conn, rows: pd.DataFrame) -> int:
    if rows.empty:
        return 0
    inserted = 0
    with conn.cursor() as cursor:
        for record in rows.to_dict(orient="records"):
            cursor.execute(
                """
                INSERT INTO feedback (alert_id, user_id, label, reason)
                SELECT %s, %s, %s, %s
                WHERE NOT EXISTS (SELECT 1 FROM feedback WHERE alert_id = %s)
                """,
                (
                    int(record["alert_id"]),
                    str(record["user_id"]),
                    str(record["label"]),
                    str(record["reason"]),
                    int(record["alert_id"]),
                ),
            )
            inserted += cursor.rowcount
    conn.commit()
    return inserted


def write_summary(path: Path, summary: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def run_seed(config: ValidationSeedConfig) -> dict[str, object]:
    results_path = Path(config.results_path)
    summary_path = Path(config.summary_path)
    context = parse_validation_results(results_path)

    with db_connect() as conn:
        attack_candidates = load_alert_candidates(conn, context.attack_source_ip, context.validation_started_at)
        benign_candidates = load_alert_candidates(conn, context.benign_source_ip, context.validation_started_at)
        rows = build_feedback_rows(attack_candidates, benign_candidates, config.user_id)
        inserted = 0 if config.dry_run else persist_feedback_rows(conn, rows)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": asdict(config),
        "validation_context": {
            "validation_started_at": context.validation_started_at.isoformat(),
            "attack_source_ip": context.attack_source_ip,
            "benign_source_ip": context.benign_source_ip,
        },
        "attack_alert_candidates": int(len(attack_candidates)),
        "benign_alert_candidates": int(len(benign_candidates)),
        "attack_candidates_without_feedback": int((attack_candidates.get("existing_feedback", 0) == 0).sum()) if not attack_candidates.empty else 0,
        "benign_candidates_without_feedback": int((benign_candidates.get("existing_feedback", 0) == 0).sum()) if not benign_candidates.empty else 0,
        "seed_rows": int(len(rows)),
        "inserted_feedback_rows": int(inserted),
        "seed_preview": rows.head(20).to_dict(orient="records") if not rows.empty else [],
        "notes": [
            "attack-source alerts are labelled true_positive because they come from controlled malicious validation traffic",
            "benign-source alerts are labelled false_positive because they come from the controlled baseline phase",
            "this script is intended to create recent reviewed evidence for temporal retraining validation",
        ],
    }
    write_summary(summary_path, summary)
    return summary


def parse_args() -> ValidationSeedConfig:
    parser = argparse.ArgumentParser(description="Seed reviewed feedback from controlled real-log validation outputs")
    parser.add_argument("--results-path", default=str(DEFAULT_RESULTS_PATH))
    parser.add_argument("--summary-path", default=str(DEFAULT_SUMMARY_PATH))
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return ValidationSeedConfig(
        results_path=args.results_path,
        summary_path=args.summary_path,
        user_id=args.user_id,
        dry_run=args.dry_run,
    )


def main() -> dict[str, object]:
    config = parse_args()
    summary = run_seed(config)
    print("=" * 72)
    print("VALIDATION FEEDBACK BOOTSTRAP")
    print("=" * 72)
    for key in [
        "attack_alert_candidates",
        "benign_alert_candidates",
        "attack_candidates_without_feedback",
        "benign_candidates_without_feedback",
        "seed_rows",
        "inserted_feedback_rows",
    ]:
        print(f"{key}: {summary[key]}")
    print(f"summary: {config.summary_path}")
    return summary


if __name__ == "__main__":
    main()
