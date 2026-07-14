-- Legacy compatibility bootstrap.
-- The database schema source of truth is now Alembic under /alembic/versions.
-- docker-compose no longer mounts this file automatically.
CREATE EXTENSION IF NOT EXISTS timescaledb;
