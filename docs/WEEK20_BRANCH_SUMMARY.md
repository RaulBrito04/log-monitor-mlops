# Week 20 Branch Summary

## Branch
- Branch: `week20-web-log-diversity`

## Goal
Extend real-log support beyond a single access-log format, validate the full pipeline with those new web log variants, and close the rule-engine duplication gap discovered during smoke testing.

## What was undocumented before this update
This branch introduced work that was already implemented and validated locally but not yet written down in a branch summary:
- support for additional web log formats in the ingester
- parser and normalization coverage for structured web JSON access logs
- end-to-end smoke validation for `web_json` and `nginx_combined`
- the duplicate-alert issue in the SQL rule engine when the same window was re-evaluated

## Changes implemented in this branch

### 1. Web log diversity in ingestion
Updated `src/log_processor/ingester.py` to support:
- `json`
- `web_json`
- `apache_combined`
- `apache_common`
- `nginx_combined`
- `auto`

The `auto` path now distinguishes between:
- structured web JSON access logs
- generic JSON logs
- classic access-log text lines

### 2. Structured web JSON normalization
Added normalization helpers so Nginx-style JSON fields such as:
- `remote_addr`
- `request`
- `time_local`
- `time_iso8601`
- `request_time`
- `http_user_agent`
- `body_bytes_sent`

are mapped into the internal ingestion schema without requiring a separate pipeline.

### 3. Unit test expansion
Extended `tests/unit/test_ingester.py` to cover:
- `apache_common`
- `nginx_combined`
- `web_json`
- `auto` detection for structured web JSON
- end-to-end batch ingestion for `web_json`

### 4. Rule-engine idempotency fix
The smoke test for the new formats exposed a real issue: when the rule engine re-processed the same time window, identical rule alerts could be inserted more than once.

This was fixed by updating `src/detection/rule_engine.py` and `docker/init.sql` to:
- add `alerts.dedup_key`
- add a partial unique index for rule alerts: `idx_alerts_rule_dedup`
- generate a deterministic `dedup_key` per rule hit
- use `INSERT ... ON CONFLICT ... DO UPDATE` semantics for rule alerts
- prepare the schema automatically at runtime through `ensure_alert_dedup_schema(...)` so the fix also works on the current existing database, not only on a fresh init

## Why this dedup strategy was chosen
The immediate production-risk was silent duplication of the same incident when the same selected logs were evaluated again.

The implemented `dedup_key` is based on:
- the alert type
- the main grouped fields such as IP / endpoint / method / user agent where relevant
- the ordered set of contributing `log_ids`

This gives exact-match idempotency for repeated evaluation of the same detection set.

Practical effect:
- if the exact same incident window is re-run, no duplicate alert is created
- if the contributing log set changes, a new alert can still be created

This is a responsible first fix because it removes false duplication without inventing a loose incident-bucketing heuristic that could merge genuinely different events.

## Validation completed

### Unit tests
Commands run:

```bash
./venv/bin/python -m py_compile src/detection/rule_engine.py src/log_processor/ingester.py tests/unit/test_rule_engine.py tests/unit/test_ingester.py
./venv/bin/python -m pytest --no-cov tests/unit/test_rule_engine.py tests/unit/test_ingester.py -q
```

Result:
- `24 passed`

### Full-pipeline smoke validation for new log sources
The stack was validated with two new sources:
- `web_json`
- `nginx_combined`

Observed outcome from the smoke run:
- `7` malicious `web_json` logs ingested successfully
- `2` benign `nginx_combined` logs ingested successfully
- all `9` logs were persisted into `raw_logs`
- hybrid scoring rows were created for both sources
- the malicious `web_json` sample triggered rule alerts for:
  - `brute_force`
  - `path_traversal`
  - `sql_injection`
  - `suspicious_user_agent`

### Real smoke validation of the dedup fix
A targeted brute-force sample with IP `198.51.100.230` was inserted into the live development stack and the same brute-force rule was executed twice.

Observed result:
- first execution: `1` alert created
- second execution: `0` alerts created
- final alert count for that IP and alert type: `1`

This confirms that the rule engine is now idempotent for repeated evaluation of the same selected log set.

## How to test the pipeline end to end for the new sources
For each format (`web_json`, `nginx_combined`, optionally `apache_common`), the responsible end-to-end check is:

1. ingest a known benign or malicious sample through the normal ingester path
2. verify persistence in `raw_logs` with the expected `source_format` and normalized HTTP fields
3. run the rule engine and confirm expected alerts for malicious samples
4. verify `hybrid_scores` exists for the same `log_id` set
5. confirm metrics and dashboard views reflect the newly ingested data
6. re-run the same rule window and confirm the alert count does not increase for the same incident

## What this branch achieves in practice
- The platform is no longer limited to a single access-log flavor during real-log validation.
- The same ingestion pipeline can now handle classic text access logs and structured web JSON logs.
- The rule engine no longer gives false confidence by duplicating the same alert every time a window is re-evaluated.
- The validation story is stronger because parser support, persistence, rules, hybrid scoring, and rule idempotency were all exercised on the running stack.

## Remaining note
This dedup implementation solves duplicate re-insertion of the same detection set.
A later optional refinement could add higher-level incident bucketing for long-running evolving campaigns, but that is a separate product decision and should not be mixed with the idempotency fix itself.
