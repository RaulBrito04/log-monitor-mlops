# Bulk Ingestion Mode

## Goal
Provide a separate high-throughput ingestion path for backfill, benchmarks, and large offline imports without replacing the online ingestion default.

## Runtime behaviour
- Default online path: `execute_values`
- Optional offline bulk path: PostgreSQL `COPY`

## Command examples

```bash
./venv/bin/python src/log_processor/ingester.py logs/app.log --batch-size 500
./venv/bin/python src/log_processor/ingester.py logs/real/validation_access.log --format nginx_combined --insert-method copy --batch-size 5000
```

## Why it is separate
- `COPY` is faster for large historical loads.
- `execute_values` remains the safer default for the normal online path.
- This keeps the real-time pipeline claim honest while still offering a throughput-oriented offline mode.

## What it is good for
- backfill of historical logs
- benchmark ingestion
- stress/load experiments
- reproducible demo setup with larger input volumes
