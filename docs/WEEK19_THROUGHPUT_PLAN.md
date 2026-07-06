# Week 19 - Throughput and Scalability Plan

## Objective
- Increase sustainable `logs/s` without breaking real-time detection claims.
- Measure the actual bottleneck distribution before larger refactors.
- Improve throughput first in the online path; treat `COPY` as a separate bulk-ingestion concern.

## Current bottleneck hypothesis
- Raw ingestion is already batched via `execute_values`, so PostgreSQL insert throughput is not the first likely ceiling.
- The online path has an explicit throughput ceiling in `realtime_hybrid`:
  - fetch limit of `500` logs per cycle
  - default poll interval of `30s`
  - theoretical ceiling of about `16.7 logs/s`
- The hybrid runtime also still does too much work per log:
  - feature extraction over the batch
  - per-log ML scoring
  - per-log rule lookup
  - per-log persist/commit of `hybrid_scores`
- The rule engine scans the recent window repeatedly, which can create DB contention if its frequency is raised without changing the processing model.

## Non-goals
- Do not replace the whole online pipeline with `COPY`.
- Do not raise polling frequency and rule-engine frequency together without instrumentation.
- Do not add write-heavy indexes before measuring the read/write trade-off.

## Success metrics
- Ingestion throughput: sustained `logs/s`
- End-to-end latency: raw log inserted -> hybrid score persisted
- Rule-engine timing: per-rule execution time and total cycle time
- ML timing:
  - feature engineering time per batch
  - inference time per batch
  - persist time per batch
- Reliability:
  - parse success rate
  - batch failure rate
  - retry/sub-batch recovery rate
- DB pressure:
  - query duration
  - container CPU/RAM
  - lock/contention symptoms

## Measurement-first baseline
Before changing the runtime, instrument each stage and capture a baseline run.

### Instrumentation targets
- `src/log_processor/ingester.py`
  - parse time
  - insert time
  - batch size
- `src/ml/realtime_hybrid.py`
  - fetch time
  - feature extraction time
  - batch scoring time
  - batch persist time
  - logs processed per cycle
- `src/ml/hybrid_pipeline.py`
  - per-batch or per-stage timing, not only per-log logic
- `src/detection/rule_engine.py`
  - total cycle time
  - per-rule query time
- `src/monitoring/metrics.py`
  - expose Prometheus histograms/gauges for the new timings

### Baseline scenarios
- `10k` logs
- `50k` logs
- `100k` logs if feasible
- Two traffic profiles:
  - mostly benign
  - mixed benign + controlled malicious traffic

### Baseline outputs
- A markdown report with:
  - config used
  - throughput
  - p50/p95 stage timings
  - detected bottleneck ranking

## Phase 1 - Safe quick wins
Goal: remove the obvious ceiling and batch the expensive commit path without changing the detection semantics too aggressively.

### 1. Decouple runtime frequencies
- Introduce separate runtime knobs:
  - `HYBRID_POLL_INTERVAL_SEC`
  - `HYBRID_FETCH_LIMIT`
  - `RULE_ENGINE_INTERVAL_SEC`
  - keep the rule-engine analysis window explicit
- Keep rule-engine scheduling independent from hybrid polling.

### 2. Raise throughput ceiling conservatively
- Start with moderate values, not extreme ones:
  - fetch limit from `500` to `1000` or `2000`
  - poll interval from `30s` to `2s` or `5s`
- Re-measure after each step.

### 3. Batch persist `hybrid_scores`
- Replace per-log commit with batch persistence.
- Use one commit per batch under normal conditions.
- Add guarded failure handling:
  - if a full batch fails, retry by sub-batches
  - isolate bad rows rather than losing the whole batch

### 4. Document throughput vs. latency trade-off
- Record explicitly in docs/report:
  - larger batches improve `logs/s`
  - larger batches increase time-to-detection
- Keep batch sizes aligned with the project’s “real-time” claim.

### Phase 1 acceptance criteria
- Measured throughput is materially above the current ceiling.
- No batch-wide data loss on a single bad row.
- Rule engine frequency remains controlled and does not spike DB contention.
- Stage timings are visible in Prometheus/Grafana or equivalent logs.

## Phase 2 - Structural improvements
Goal: remove the per-log architecture bottlenecks and prepare the pipeline for higher sustained rates.

### 1. Vectorized ML scoring
- Score the entire batch at once instead of building a one-row DataFrame per event.
- Persist predictions in batch.

### 2. Batch rule lookup for hybrid scoring
- Stop querying `alerts` once per `log_id`.
- Load rule hits for the whole batch and build an in-memory mapping.

### 3. Incremental rule engine with watermark tolerance
- Move away from rescanning the full recent window every cycle.
- Use:
  - a cursor/watermark
  - a lateness tolerance window
- The tolerance is needed to avoid silent false negatives when logs arrive slightly out of order.

### 4. Query and index tuning
- Measure the hottest rule-engine and dashboard queries first.
- Only then consider:
  - `pg_trgm` for `ILIKE '%...%'` patterns
  - indexes supporting `log_ids` lookup
- Re-measure insert throughput after adding indexes.

### 5. Optional bulk ingestion path
- Add a separate bulk/backfill path using `COPY`.
- Use it for:
  - historical backfill
  - benchmark ingestion
  - stress tests
- Do not replace the online path with `COPY` unless measurement proves it is necessary.

### Phase 2 acceptance criteria
- Hybrid runtime throughput scales beyond the Phase 1 ceiling.
- Rule-engine work is incremental rather than repetitive window rescanning.
- Added indexes show a net benefit after write-side measurement.
- Bulk path exists as an optional mode, not a hidden change to the online runtime.

## Suggested implementation order
1. Instrumentation and baseline report
2. Decouple frequencies
3. Moderate increase of hybrid fetch size and lower poll interval
4. Batch persist with retry/sub-batch fallback
5. Vectorized ML scoring
6. Batch rule lookup
7. Incremental rule engine with watermark tolerance
8. Index tuning based on measured hotspots
9. Optional `COPY` bulk mode

## Files most likely to change
- `src/log_processor/ingester.py`
- `src/ml/realtime_hybrid.py`
- `src/ml/hybrid_pipeline.py`
- `src/detection/rule_engine.py`
- `src/monitoring/metrics.py`
- `docker/init.sql`
- `tests/test_stress.py`
- new benchmark/report docs under `docs/`

## Risks to watch
- Higher throughput but worse real-time latency
- More frequent cycles increasing DB contention
- Batch failures causing silent data loss if fallback is weak
- Incremental rule-engine logic missing late-arriving events
- Indexes helping reads but degrading write throughput

## Deliverables
- A baseline benchmark report
- Runtime instrumentation committed to the repo
- Phase 1 throughput improvement with measured before/after numbers
- A follow-up decision on whether Phase 2 or `COPY` bulk mode has the highest ROI
