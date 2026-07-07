# Week 19 Branch Summary

## Goal
Improve end-to-end throughput of the ingestion + hybrid detection path, confirm the real bottleneck, and validate the optimized pipeline in the containerized stack.

## What was prepared first
- Added throughput instrumentation for ingestion, realtime hybrid processing, and hybrid pipeline stages.
- Added a baseline collector script to replay project logs and persist comparable throughput snapshots.
- Added a markdown baseline report generator so results can be reused in docs and demos.

## Diagnosis
The initial issue was not raw database insert speed alone.
The main bottlenecks were:
- an artificial steady-state ceiling in the realtime loop (`fetch_limit / poll_interval`)
- per-log processing overhead in the hybrid path
- repeated expensive feature engineering work per batch
- avoidable fetch overhead when polling PostgreSQL

## Changes implemented in this branch

### 1. Throughput instrumentation and baseline collection
- Added `scripts/collect_week19_baseline.py`
- Added `src/monitoring/throughput_report.py`
- Updated `docs/WEEK19_BASELINE_RESULTS.md`
- Persisted component throughput snapshots into runtime metrics for later monitoring/reporting

### 2. Runtime cadence controls
- Added environment-driven realtime controls in `src/ml/realtime_hybrid.py`
  - `HYBRID_POLL_INTERVAL_SEC`
  - `HYBRID_FETCH_LIMIT`
- Added environment-driven rule engine controls in `src/detection/rule_engine.py`
  - `RULE_ENGINE_INTERVAL_SEC`
  - `RULE_ENGINE_WINDOW`
- Wired the new knobs into `docker/docker-compose.yml`

### 3. Hybrid pipeline optimization
- Replaced row-by-row persistence with batch persistence using `execute_values`
- Replaced per-log rule lookups with batch rule lookup
- Replaced per-log ML scoring with vectorized batch scoring
- Persisted richer runtime snapshots for throughput diagnostics

### 4. Feature engineering optimization
- Reworked aggregation logic to use faster `groupby(...).transform(...)` patterns
- Cached endpoint entropy by unique endpoint instead of recalculating repeatedly
- Simplified behavioral counters with vectorized operations
- Avoided slower baseline calculations based on `transform(lambda ...)`

### 5. Fetch path optimization
- Reused a PostgreSQL connection for realtime polling
- Replaced `pd.read_sql(...)` with cursor fetch + `DataFrame.from_records(...)`
- Reduced avoidable overhead in the fetch stage

### 6. Real stack fixes discovered during validation
The optimized code was fast locally, but the containerized `ml-pipeline` initially failed in the real stack for operational reasons:
- the image only copied `src/ml/`, so `src.monitoring` imports failed
- the pipeline then failed writing `runtime_metrics.json` because `/app/data` came from a bind mount not writable by the non-root container user

These were fixed by:
- updating `docker/dockerfiles/Dockerfile.ml-pipeline` to copy `src/`, set `PYTHONPATH=/app`, and run `python -m src.ml.realtime_hybrid`
- updating `src/monitoring/metrics.py` to allow `RUNTIME_METRICS_FILE` override
- adding a shared `runtime_metrics_data` Docker volume for `flask-app` and `ml-pipeline`
- updating `docker/dockerfiles/Dockerfile.flask` and `docker/docker-compose.yml` accordingly

## Measured results
From the latest replay-based baseline in `docs/WEEK19_BASELINE_RESULTS.md`:
- `hybrid_pipeline`: `4025.615 logs/s`
- `realtime_hybrid`: `2286.194 logs/s`
- `ingester`: `1259.708 logs/s`

Stage timings improved to approximately:
- fetch: `0.141 s`
- feature engineering: `0.404 s`
- evaluation: `1.637 s`

Configured steady-state ceiling in the daemon loop:
- `HYBRID_FETCH_LIMIT=1000`
- `HYBRID_POLL_INTERVAL_SEC=2`
- ceiling = `500 logs/s`

Important interpretation:
- the collector benchmark measures active processing throughput without the sleep interval
- the live daemon remains bounded by `fetch_limit / poll_interval` in steady state

## Real stack validation outcome
After rebuilding the containers with the fixes:
- `ml-pipeline` started correctly in Docker
- it caught up to the existing backlog
- it remained stable in the realtime loop
- `raw_logs` max id and `hybrid_scores` max log id both reached `165203`

That means the realtime pipeline was caught up at the moment of validation, with no processing lag left between ingested logs and hybrid scoring.

## Tests run
- `./venv/bin/python -m pytest --no-cov tests/unit/test_monitoring_metrics.py tests/unit/test_throughput_report.py tests/unit/test_hybrid_pipeline.py tests/unit/test_realtime_hybrid.py tests/unit/test_feature_engineering.py -q`
- Result: `33 passed`
- `docker compose -f docker/docker-compose.yml config`
- Result: compose configuration valid after the runtime metrics volume changes

## What this branch achieved in practice
- We moved the hybrid path from a slow per-log design to a genuinely batch-oriented path.
- We proved the main bottleneck was upstream processing overhead, not just inserts.
- We validated the optimized path in the real container stack, not only in local direct-run benchmarks.
- We left the stack with explicit throughput knobs and reusable measurement/reporting artifacts.

## Remaining note
A previously mentioned `~9k logs/s` ingester figure should be treated as a historical, non-equivalent measurement from a different run/setup. The comparable Week 19 measured ingester result captured in this branch is the current `1259.708 logs/s` replay benchmark.
