# Final Quality and Reproducibility

## Goal
Provide one repeatable command set to verify that the project is still in a releasable state before demo, submission, or merge.

## Main command

```bash
bash scripts/run_final_quality_gate.sh
```

## Last verified run

Last verified locally on this branch with:

```bash
bash scripts/run_final_quality_gate.sh
```

Observed outcome:
- `py_compile` passed on `src/`, `tests/`, and `scripts/`
- dataset quality loaded from `data/reviewed_feedback_dataset.csv`
- unit/app/dashboard/MLflow suites: `183 passed`
- integration suite: `2 passed`
- Alembic revision check reached `20260707_0001 (head)`
- `docker compose -f docker/docker-compose.yml config` passed

Dataset-quality notes from the same run:
- missing values detected in `exclude_reason` and `original_dataset_label`
- several control columns are constant by design in the reviewed-feedback artifact
- label balance reported as `21.61%`
- time range reported from `2026-03-01` to `2026-07-01`

## What it checks
1. `py_compile` over `src/`, `tests/`, and `scripts/`
2. lightweight dataset-quality checks when a compatible dataset artifact is available
3. unit + Flask app + dashboard + MLflow tests
4. integration tests
5. Alembic migration upgrade/current check
6. `docker compose config` validation

## Optional skips
These are useful when the environment is partially unavailable but you still want a fast local check.

```bash
SKIP_INTEGRATION=1 bash scripts/run_final_quality_gate.sh
SKIP_DOCKER=1 bash scripts/run_final_quality_gate.sh
SKIP_DATASET_QUALITY=1 bash scripts/run_final_quality_gate.sh
```

## Notes
- The dataset-quality step is intentionally tolerant when no local dataset artifact exists yet.
- The script validates migrations against the configured database; it does not replace a fresh end-to-end `docker compose up` smoke test.
- For final delivery, pair this gate with the demo and evidence documents listed in `docs/FINAL_EVIDENCE_INDEX.md`.
