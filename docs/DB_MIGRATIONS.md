# Database Migrations

## Goal
Move database schema management from ad-hoc SQL bootstrap into a versioned migration workflow suitable for real environments.

## Source of truth
- Official schema source: `alembic/versions/`
- Runtime configuration: `alembic.ini` + environment variables
- Legacy file: `docker/init.sql` is no longer the source of truth and is not mounted automatically by Docker Compose

## Local commands

```bash
./venv/bin/alembic upgrade head
./venv/bin/alembic current
./venv/bin/alembic history
./venv/bin/alembic downgrade -1
```

Environment variables used by Alembic:
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- optional override: `ALEMBIC_DATABASE_URL`

## Docker Compose bootstrap
`docker compose -f docker/docker-compose.yml up -d` now includes a one-shot service called `db-migrate`.

Expected behaviour:
- PostgreSQL starts first
- `db-migrate` runs `alembic upgrade head`
- runtime services wait for the migration job to complete successfully before starting

`db-migrate` is expected to exit with code `0`; it is a bootstrap job, not a long-running API service.

## Tests and CI
- `tests/conftest.py` now creates the test database and applies `alembic upgrade head`
- GitHub Actions runs an Alembic smoke check before pytest
- the CI build stage now also builds the `db-migrate` image

## How to add the next migration

```bash
./venv/bin/alembic revision -m "describe the change"
```

Good practice:
- keep each migration small
- prefer additive changes first
- include an explicit downgrade when practical
- validate migrations against an existing populated database before merge
