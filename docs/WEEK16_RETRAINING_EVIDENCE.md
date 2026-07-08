# Week 16 Retraining Evidence

## Context
This document captures the verified outcome of the reviewed-feedback retraining workflow after enough recent reviewed samples were accumulated.

## Evidence sources
- Runtime summary: `docs/WEEK16_PROGRESS.md`
- Safe retraining design: `docs/WEEK16_HUMAN_IN_THE_LOOP.md`
- Report artifact: `experiments/feedback_retraining_report.json`

## Verified outcome
- Report status: `candidate_built`
- Promotion result: `promotable = true`
- Eligible feedback events: `106`
- Ready training samples: `1328`

## Split quality
From `experiments/feedback_retraining_report.json`:
- `train`: `796` rows (`199` positives, `597` negatives)
- `reviewed_holdout`: `266` rows (`67` positives, `199` negatives)
- `temporal_holdout`: `266` rows (`21` positives, `245` negatives)

This matters because the first safe retraining cycle was intentionally blocked when the temporal holdout lacked positive examples. The current run closes that gap and makes the promotion gate meaningful.

## Operational metrics published
Latest verified retraining metrics:
- `logmonitor_ml_retraining_promotable = 1`
- `logmonitor_ml_retraining_feedback_events = 106`
- `logmonitor_ml_retraining_ready_log_samples = 1328`
- `logmonitor_ml_retraining_reviewed_f1_delta = 0.9336105260278469`
- `logmonitor_ml_retraining_temporal_f1_delta = 0.8536585365853658`
- `logmonitor_ml_retraining_temporal_precision_delta = 0.9210526315789473`

## Interpretation
- The project now has a real reviewed-feedback pipeline, not only a feedback table.
- The retraining workflow remains safe: candidate-only, explicit promotion logic, no silent overwrite of the deployed runtime model.
- The temporal holdout now contains both positive and negative reviewed samples, so the promotion result is based on evidence instead of optimistic reviewed-holdout performance alone.

## Important limitation kept explicit
This workflow evaluates an offline `RandomForest` candidate. The current realtime runtime still uses rules plus `IsolationForest`, so the retraining result is evidence of a responsible model-governance loop, not of automatic runtime replacement.
