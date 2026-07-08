# Week 22 - Schema Discipline and Log Adapter Contracts

## What changed
- Removed remaining runtime schema mutation from `rule_engine` and `flask_app`.
- Added read-only schema validation in `src/db/schema_checks.py`.
- Added contract-style tests for supported log adapters and normalization behaviour.

## Why this matters
- Runtime services no longer hide missing migrations with ad-hoc `ALTER TABLE` or `CREATE INDEX`.
- Startup now fails fast when the database schema is not at the expected migrated revision.
- Log-source support is now protected by explicit parser contracts, which reduces regression risk as more web log variants are added.

## Runtime behaviour now
- `src/detection/rule_engine.py` requires:
  - `alerts.dedup_key`
  - `idx_alerts_rule_dedup`
- `src/flask_app/app.py` requires:
  - incident workflow columns in `alerts`
  - `alert_incident_history`
  - incident history indexes

If any of these objects are missing, the service raises a schema error with a migration hint instead of changing the database on the fly.

## Tests added or updated
- `tests/unit/test_schema_checks.py`
- `tests/unit/test_log_adapter_contracts.py`
- updated Flask app fixtures to mock startup schema validation
- updated `tests/unit/test_rule_engine.py` to verify runtime schema validation happens before execution

## Validation checklist
- run targeted unit tests for schema checks, rule engine, flask app, security, ingester and adapter contracts
- run `py_compile` on touched Python modules
- run `docker compose -f docker/docker-compose.yml config`


## Validation results
- `./venv/bin/python -m py_compile src/db/schema_checks.py src/detection/rule_engine.py src/flask_app/app.py tests/unit/test_rule_engine.py tests/unit/test_security.py tests/test_flask_app.py tests/unit/test_schema_checks.py tests/unit/test_log_adapter_contracts.py` passed
- `./venv/bin/python -m pytest --no-cov tests/unit/test_schema_checks.py tests/unit/test_log_adapter_contracts.py tests/unit/test_rule_engine.py tests/unit/test_security.py tests/test_flask_app.py tests/unit/test_ingester.py -q` passed with `97 passed`
- `./venv/bin/python -m pytest --no-cov tests/integration/test_pipeline_integration.py -q -m integration` passed with `2 passed`
- `./venv/bin/python -m pytest --no-cov tests/test_dashboard.py -q` passed with `16 passed`
- `docker compose -f docker/docker-compose.yml config` passed

## Suggested next step
- Next higher-value production-ready step after this branch: secrets and reverse-proxy hardening, because parser contracts and migration discipline are now covered.
