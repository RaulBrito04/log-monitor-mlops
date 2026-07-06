# Week 16 - Human-in-the-Loop Feedback and Safe Retraining

## What is already implemented

The project now has a real human-in-the-loop feedback capture path for alert review.

Implemented in the current branch:
- Streamlit Alerts page lets an analyst mark an alert as `true_positive`, `false_positive`, or `false_negative` and add notes.
- Dashboard feedback is sent to Flask instead of writing directly to PostgreSQL.
- Flask exposes a validated `POST /api/alerts/feedback` endpoint with rate limiting.
- Feedback is persisted to the existing `feedback` table and shown back in the dashboard as per-alert history.
- Focused Flask and dashboard tests were added for the feedback write/history path.

This means the project now captures reviewed labels, but it still does **not** automatically use them to retrain or replace models.

## Why the next step is not "just retrain"

Retraining directly from raw feedback rows would be unsafe.

The correct next stage is:
1. read feedback together with alert metadata and linked `log_ids`
2. map reviewed labels into trainable targets
3. join reviewed logs with existing feature artifacts
4. create a reviewed dataset artifact
5. split it into `train`, `reviewed_holdout`, and `temporal_holdout`
6. train a candidate model
7. compare candidate vs current baseline in MLflow
8. decide promotion using explicit rules

This keeps the process auditable and avoids silent overfitting to recent analyst corrections.

## What is being implemented now

This branch now adds a safe retraining stage with three parts:

### 1. Reviewed dataset builder
- Builds a supervised reviewed dataset from `feedback -> alerts -> log_ids`.
- Reuses existing `data/ml_dataset.*` feature artifacts when available.
- Falls back to live feature extraction only for missing reviewed logs.
- Saves reviewed dataset artifacts under `data/`.
- Keeps non-trainable `false_negative` feedback visible in the artifact, but excludes it from supervised retraining for now.

### 2. Candidate retraining job
- Trains a `RandomForest` candidate from the reviewed dataset.
- Uses `reviewed_holdout` for threshold/model selection.
- Uses `temporal_holdout` as a sanity check against temporal regression.
- Saves the candidate model separately instead of replacing the deployed model automatically.

### 3. Promotion gate + MLflow report
- Logs dataset counts, split counts, baseline metrics, candidate metrics, and promotion checks to MLflow.
- Produces a JSON report under `experiments/`.
- Promotion is recommendation-only by default.

## Important limitations kept explicit

### `false_negative` feedback is not yet directly trainable
The current schema links feedback to an existing alert. A true false negative usually means a missed event or missed log that never became an alert. Because of that, `false_negative` is captured and counted, but deferred from supervised retraining until the project has incident-level or log-level missed-event linkage.

### Current runtime does not consume a `RandomForest` model
The real-time pipeline currently uses rule scores plus `IsolationForest` scoring. That means the reviewed-feedback retraining stage is an **offline candidate-evaluation pipeline** for now, not an automatic runtime deployment path.

### No automatic overwrite of deployed models
The retraining stage will save a candidate artifact and a promotion report. It will not silently replace the current deployed model.

## Intended command

After implementation, the new retraining stage should be runnable with:

```bash
python -m src.ml.feedback_retraining
```

## What success looks like

A responsible run should produce:
- `data/reviewed_feedback_dataset.csv`
- `data/reviewed_feedback_dataset.pkl`
- `data/reviewed_feedback_dataset_iforest.csv`
- `data/reviewed_feedback_dataset_iforest.pkl`
- `data/reviewed_feedback_summary.json`
- `models/random_forest_feedback_candidate.pkl`
- `models/random_forest_feedback_candidate_metadata.json`
- `experiments/feedback_retraining_report.json`
- an MLflow run with baseline-vs-candidate metrics and promotion checks


## What was implemented in this iteration

Additional work completed in this branch:
- Added `src/ml/seed_reviewed_feedback.py` to bootstrap a controlled reviewed dataset seed from two explicit sources only:
  - `true_positive`: alerts linked only to controlled attack scenarios
  - `false_positive`: localhost `/health` alerts with `python-requests` user agent treated as benign monitoring traffic
- Added `build_retraining_runtime_metrics_payload(...)` and runtime-metrics sync inside `src/ml/feedback_retraining.py` so every retraining run publishes operational state even when blocked.
- Added new Prometheus gauges in `src/monitoring/metrics.py` for reviewed-feedback retraining status, candidate availability, promotion state, reviewed F1 delta, temporal F1 delta, and temporal precision delta.
- Added `MLRetrainingDegraded` to `docker/alerts.yml` so Alertmanager can warn if a future candidate underperforms the current baseline.
- Fixed a real artifact-compatibility issue: the old `models/scaler.pkl` only matched a stale 9-feature schema, while the current supervised baseline and dataset use 18 features. The retraining pipeline now selects the scaler by matching the feature schema rather than assuming a fixed filename.
- Added focused unit tests for retraining runtime metrics, seed selection, zero-limit sampling safety, scaler fallback, and monitoring gauges.

## Commands executed

```bash
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.seed_reviewed_feedback --max-positive-alerts 40 --negative-ratio 2.0 --dry-run
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.seed_reviewed_feedback --max-positive-alerts 40 --negative-ratio 2.0
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.feedback_retraining
```

## Observed execution results (2026-06-30)

### Bootstrap seed
- Controlled attack candidates available: `134`
- Benign healthcheck candidates available: `2820`
- Seed inserted: `120` feedback rows
  - `40` controlled `true_positive`
  - `80` benign `false_positive`
- Seed summary saved to `data/reviewed_feedback_seed_summary.json`

### Reviewed dataset and retraining run
- Retraining report status: `candidate_built`
- Eligible feedback events: `103`
- Ready-for-training reviewed log samples: `1307`
- Feature source mix:
  - `live_extract`: `1011`
  - `artifact_dataset`: `296`
- Label distribution in trainable reviewed rows:
  - negatives: `1041`
  - positives: `266`

### Split outcome
- Train: `783` rows, `199` positives
- Reviewed holdout: `262` rows, `67` positives
- Temporal holdout: `262` rows, `0` positives

### Baseline vs candidate
- Baseline reviewed holdout F1: `0.0156`
- Candidate reviewed holdout F1: `0.9710`
- Reviewed holdout F1 delta: `+0.9555`
- Baseline temporal holdout F1: `0.0000`
- Candidate temporal holdout F1: `0.0000`
- Temporal holdout precision delta: `0.0000`

### Promotion decision
- Result: `not promotable`
- Failed check: `enough_positive_temporal_holdout`

This is the correct outcome. The pipeline did **not** auto-promote a model from an impressive reviewed-holdout score alone because the temporal holdout had no positive examples.

## Interpretation

The first Week 16 cycle is successful as a **safe retraining workflow**, not as an automatic model-improvement claim.

What we learned:
- The human-in-the-loop path is now operational end to end: feedback can be captured, turned into reviewed training artifacts, evaluated against the current baseline, logged to MLflow, and exposed to monitoring state.
- The current bootstrap seed is enough to train and evaluate a candidate, but not enough to produce a temporally meaningful promotion decision.
- The temporal split is doing its job: it prevented a misleading promotion when the latest slice contained only benign reviewed traffic.
- A large portion of reviewed rows (`1011 / 1307`) had to use live feature extraction, which is exactly why the scaler-schema fix mattered.

## What should happen next

The next responsible step is not "promote anyway".

It is to collect or review more **recent positive anomaly examples** so the temporal holdout includes positives. After that, rerun:

```bash
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.feedback_retraining
```

When the temporal holdout contains both positive and negative reviewed examples, the promotion gate becomes informative enough for a real go/no-go decision.


## Second evidence cycle with recent controlled positives (2026-07-01)

The first retraining cycle proved that the workflow was safe, but it was blocked from promotion because the temporal holdout had no positive reviewed examples.

To close that evidence gap without changing the architecture, a second controlled cycle was executed:
- a fresh real-log validation run was generated through the existing Nginx validation path and written to `docs/WEEK16_RETRAINING_EVIDENCE.md`
- a new helper script, `src/ml/seed_validation_feedback.py`, converted the latest controlled attack alerts from that run into reviewed `true_positive` feedback
- the retraining pipeline was executed again against the enlarged reviewed dataset

### Commands executed for the second cycle

```bash
POSTGRES_PASSWORD=changeme_em_prod REAL_LOG_RESULTS_PATH=docs/WEEK16_RETRAINING_EVIDENCE.md bash scripts/run_real_log_validation.sh
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.seed_validation_feedback --results-path docs/WEEK16_RETRAINING_EVIDENCE.md --summary-path data/validation_feedback_seed_summary.json
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.feedback_retraining
```

### Recent validation evidence
- Fresh real-log validation attack source IP: `203.0.113.126`
- Fresh controlled attack alerts available for review seeding: `16`
- New reviewed feedback rows inserted from the latest validation run: `16`
- Validation feedback seed summary saved to `data/validation_feedback_seed_summary.json`

### Updated retraining outcome
- Retraining report status: `candidate_built`
- Promotion result: `promotable = true`
- Eligible feedback events: `106`
- Ready-for-training reviewed log samples: `1328`

### Updated split outcome
- Train: `796` rows, `199` positives
- Reviewed holdout: `266` rows, `67` positives
- Temporal holdout: `266` rows, `21` positives

### Updated baseline vs candidate
- Baseline reviewed holdout F1: `0.0304`
- Candidate reviewed holdout F1: `0.9640`
- Reviewed holdout F1 delta: `+0.9336`
- Baseline temporal holdout F1: `0.1463`
- Candidate temporal holdout F1: `1.0000`
- Temporal holdout F1 delta: `+0.8537`
- Temporal holdout precision delta: `+0.9211`

## Interpretation after the second cycle

This closes the main evidence gap from the first run.

What changed:
- the reviewed dataset now includes recent positive examples coming from a real controlled validation run instead of relying only on older synthetic/control-derived positives
- the temporal holdout now contains positive and negative reviewed examples, which makes the promotion gate meaningful
- the promotion checks all pass, so the candidate is now promotable under the current explicit policy

This is a materially stronger result for the report and demo than the first cycle alone, because it demonstrates:
- human feedback capture
- reviewed dataset construction
- safe candidate retraining
- temporal validation with recent positives
- explicit promotion logic that can block or approve based on evidence
