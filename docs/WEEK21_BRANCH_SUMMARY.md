# Week 21 Branch Summary

## Branch
- Branch: `week21-db-migrations`

## Goal
Introduce formal database schema migrations with Alembic so the project stops depending on ad-hoc SQL bootstrap as its primary schema management mechanism.

## Why this matters
This is one of the highest-ROI production-readiness upgrades in the project because it improves:
- controlled schema evolution
- repeatable environment setup
- rollback discipline
- CI confidence
- deploy readiness across development, test and containerized environments

## Changes implemented

### 1. Alembic foundation
Added:
- `alembic.ini`
- `alembic/env.py`
- `alembic/script.py.mako`
- `alembic/versions/20260707_0001_initial_schema.py`
- `src/db/config.py`

The initial revision creates the current project schema and is written defensively with `IF NOT EXISTS` clauses so it can also reconcile the existing local database in a controlled way.

### 2. Source of truth moved away from `docker/init.sql`
- `docker/init.sql` is now a legacy compatibility note instead of the active schema definition
- the Docker Compose PostgreSQL service no longer mounts `init.sql` as the main bootstrap path
- schema ownership is now explicitly versioned under Alembic

### 3. Compose bootstrap migration job
Added:
- `docker/dockerfiles/Dockerfile.db-migrate`
- `db-migrate` service in `docker/docker-compose.yml`

Bootstrap behaviour now becomes:
1. PostgreSQL becomes healthy
2. `db-migrate` runs `alembic upgrade head`
3. Flask, dashboard, ingester, rule-engine and ml-pipeline wait for migration completion

### 4. Test and CI integration
- `tests/conftest.py` now creates the test database and applies `alembic upgrade head`
- added `tests/unit/test_db_config.py`
- GitHub Actions now runs an Alembic smoke check before pytest
- GitHub Actions now builds the `db-migrate` image as part of the build stage

### 5. Documentation refresh
Added:
- `docs/DB_MIGRATIONS.md`
- `docs/WEEK21_BRANCH_SUMMARY.md`

Updated:
- `README.md`
- `docs/JURY_QA.md`

## What this achieves in practice
- The project now uses a market-standard migration tool instead of schema drift by SQL snapshot.
- Local, CI and Compose flows converge on the same migration path.
- The database lifecycle becomes auditable and easier to reason about during future feature work.
- The project is closer to a real deployable system because schema rollout is now explicit.

## Remaining note
This branch establishes the migration framework and the initial baseline revision.
The next natural follow-up is to require every schema change to ship with a dedicated Alembic revision instead of runtime schema patching inside services.


## Validation run

Commands executed:

```bash
./venv/bin/python -m py_compile src/db/config.py tests/conftest.py tests/unit/test_db_config.py alembic/env.py alembic/versions/20260707_0001_initial_schema.py
./venv/bin/python -m pytest --no-cov tests/unit/test_db_config.py tests/unit/test_rule_engine.py tests/unit/test_ingester.py -q
./venv/bin/python -m pytest --no-cov tests/integration/test_pipeline_integration.py -q -m integration
./venv/bin/alembic upgrade head
./venv/bin/alembic current
docker compose -f docker/docker-compose.yml config
```

Results:
- `py_compile`: passed
- unit tests: `27 passed`
- integration tests: `2 passed`
- Alembic local migration command: passed, current revision `20260707_0001 (head)`
- Docker Compose configuration validation: passed
