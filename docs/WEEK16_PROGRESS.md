# Week 16 Progress

## Closed in this cycle

### 1. Feedback retraining evidence
- Re-ran the real-log validation workflow and seeded reviewed feedback from the controlled validation alerts.
- Re-ran `src/ml/feedback_retraining.py`.
- Result: the reviewed-feedback candidate is now `promotable`.

Key evidence already generated:
- `docs/WEEK16_HUMAN_IN_THE_LOOP.md`
- `docs/WEEK16_RETRAINING_EVIDENCE.md`
- `experiments/feedback_retraining_report.json`

Latest verified retraining runtime metrics:
- `logmonitor_ml_retraining_promotable = 1`
- `logmonitor_ml_retraining_feedback_events = 106`
- `logmonitor_ml_retraining_ready_log_samples = 1328`
- `logmonitor_ml_retraining_reviewed_f1_delta = 0.9336105260278469`
- `logmonitor_ml_retraining_temporal_f1_delta = 0.8536585365853658`
- `logmonitor_ml_retraining_temporal_precision_delta = 0.9210526315789473`

### 2. Final operational polish
Applied fixes:
- Normalized `docker/scripts/run_ingester_loop.sh` to LF-only line endings.
- Shared `data/` into `flask-app` and `ml-pipeline` through Docker Compose so runtime metrics are visible to the monitoring stack.
- Fixed `nginx-validation` health and proxy resilience:
  - healthcheck now uses `127.0.0.1`
  - Nginx now uses Docker DNS resolver `127.0.0.11` and a dynamic upstream variable for `flask-app`
- Added `flask-app` as an explicit dependency of the dashboard service.

Runtime verification after rebuild:
- `docker compose -f docker/docker-compose.yml config` succeeded.
- `flask-app`, `dashboard`, `ingester`, and `nginx-validation` were healthy.
- `/metrics` exposed the retraining gauges listed above.
- Targeted regression suite passed: `76 passed` after closing the full incident lifecycle tests.

Important note:
- `logmonitor_ml_model_f1_score` remains `0.0` unless an operational F1 value is explicitly published to `/metrics/ml_quality`.
- This is not a retraining failure; it is a runtime publication/input issue.
- To publish it, use `scripts/generate_test_metrics.py` or the Flask endpoint directly.

### 3. Incident workflow MVP
Implemented a lightweight operator workflow directly on top of `alerts`.

Follow-up completion in this branch:
- Added an audit trail table for incident transitions (`alert_incident_history`).
- Added Prometheus metric `logmonitor_incident_alerts_total{incident_status=...}`.
- Exposed incident transition history in the dashboard alert detail view.

Data model:
- Added incident fields to `alerts` in `docker/init.sql`:
  - `incident_status`
  - `incident_owner`
  - `incident_notes`
  - `incident_updated_at`
  - `incident_updated_by`
- Added a startup-safe schema sync in the Flask app for existing databases.

API:
- New endpoint: `POST /api/alerts/incident`
- Allowed workflow:
  - `NEW -> INVESTIGATING`
  - `INVESTIGATING -> RESOLVED`
  - same-state updates are allowed
  - reopening `RESOLVED -> INVESTIGATING` is currently rejected

Dashboard:
- Alerts page now supports:
  - filter by `incident_status`
  - queue columns for incident status and owner
  - detail view for incident fields
  - inline incident update form in the alert detail panel

Functional validation:
- Real API updates executed successfully for both lifecycle transitions.
- Verified responses:
  - `updated_alert_id=3270`
  - status updated from `NEW` to `INVESTIGATING`
  - status updated from `INVESTIGATING` to `RESOLVED`
  - audit trail row written to `alert_incident_history`

## Commands used for verification

### Tests
```bash
./venv/bin/python -m pytest tests/test_flask_app.py tests/test_dashboard.py tests/unit/test_monitoring_metrics.py tests/unit/test_feedback_retraining.py tests/unit/test_feedback_seed.py tests/unit/test_validation_feedback_seed.py -q --no-cov
```

### Runtime stack
```bash
docker compose -f docker/docker-compose.yml config
docker compose -f docker/docker-compose.yml up -d --build flask-app dashboard nginx-validation
curl http://localhost:5001/metrics
```

### Incident API smoke test
PowerShell example:
```powershell
$payload = @{ alert_id = 1; incident_status = 'INVESTIGATING'; incident_owner = 'analyst'; incident_notes = 'triaged in week16 MVP validation'; user_id = 'analyst' } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri 'http://localhost:5001/api/alerts/incident' -Body $payload -ContentType 'application/json'
```

## What this means for the roadmap
- `Feedback retraining evidence`: closed for this cycle.
- `Final polish`: materially improved and demo-ready.
- `Incident workflow MVP`: now implemented with audit trail and monitoring metric by incident status.
- Highest-ROI remaining items after this are external validation/benchmark expansion and explanation extras, not the core feedback loop.

