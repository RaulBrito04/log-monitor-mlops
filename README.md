# Log Monitor MLOps

[![CI Pipeline](https://github.com/RaulBrito04/log-monitor-mlops/actions/workflows/ci.yml/badge.svg)](https://github.com/RaulBrito04/log-monitor-mlops/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-86.00%25-brightgreen)](https://github.com/RaulBrito04/log-monitor-mlops/actions)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED.svg?logo=docker)](docker/docker-compose.yml)
[![Security: Bandit](https://img.shields.io/badge/bandit-0%20HIGH%2F0%20MEDIUM-brightgreen.svg)](https://github.com/PyCQA/bandit)

Open-source system for hybrid anomaly detection in web application logs. It combines deterministic SQL rules, machine learning, explainability, human feedback, assisted retraining and operational observability.

The goal is not only to train a model. The project demonstrates an MLOps pipeline where logs are ingested, structured, scored by rules and ML, explained, reviewed by humans and monitored through operational metrics.

---

## Final Status

| Area | Status | Evidence |
|---|---|---|
| Docker stack | Operational | 12 Docker services |
| API | Operational | Flask + OpenAPI/Swagger |
| Ingestion | Operational | `execute_values` online path + `COPY` for backfill |
| Rule engine | Operational | 6 SQL rules |
| Hybrid ML | Operational | Isolation Forest + Random Forest |
| Dashboard | Operational | Streamlit |
| MLOps | Operational | MLflow + model/metric tracking |
| Observability | Operational | Prometheus + Grafana + Alertmanager |
| Human feedback | Implemented | FP/FN/TP review and alert history |
| Retraining | Assisted | validated candidate, no automatic promotion |
| Quality gate | Validated | 208 tests, 86.00% coverage, Pylint 9.18/10 |

---

## Quick Start

```bash
git clone https://github.com/RaulBrito04/log-monitor-mlops.git
cd log-monitor-mlops

cp docker/.env.example docker/.env
# Edit docker/.env with local secrets and credentials

docker compose -f docker/docker-compose.yml up -d
docker compose -f docker/docker-compose.yml ps
```

Main interfaces:

| Service | URL |
|---|---|
| Flask API | http://localhost:5001 |
| Swagger/OpenAPI | http://localhost:5001/docs/api |
| Streamlit dashboard | http://localhost:8501 |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| MLflow | http://localhost:5000 |

Health check:

```bash
curl http://localhost:5001/health
curl -s http://localhost:5001/openapi.json | head
```

---

## Architecture

12 Docker services organized into 5 logical layers:

```text
Logs / Client
      |
      v
Flask API + Ingester
      |
      v
PostgreSQL / TimescaleDB
      |
      +--> SQL Rule Engine ----+
      |                         |
      +--> Hybrid ML Scoring --+--> Alerts / Hybrid Scores
                                       |
                                       v
                     Dashboard + Prometheus + Grafana + MLflow
```

Core services:

| Service | Role |
|---|---|
| `flask-app` | API, log generation, feedback/incident endpoints and metrics |
| `ingester` | JSON, Apache/Nginx and bulk log ingestion |
| `rule-engine` | deterministic SQL security rules |
| `ml-pipeline` | real-time hybrid scoring |
| `dashboard` | Streamlit operator UI |
| `postgres` | logs, alerts, scores, feedback and incident persistence |
| `mlflow` | experiment, metric and model artifact tracking |
| `prometheus` | metrics scraping |
| `grafana` | operational dashboards |
| `alertmanager` | SLO and operational alerts |

---

## Detection Pipeline

Main flow:

```text
HTTP request / log file
        |
        v
raw_logs
        |
        +--> SQL rules --> alerts
        |
        +--> feature engineering --> Isolation Forest / Random Forest
                                      |
                                      v
                              hybrid_scores
                                      |
                                      v
                        dashboard, feedback, metrics, incidents
```

Core files:

```text
src/flask_app/app.py             # Flask API, health checks, metrics and endpoints
src/log_processor/ingester.py    # log ingestion into raw_logs
src/detection/rule_engine.py     # 6 deterministic SQL rules
src/ml/feature_engineering.py    # log-to-feature transformation
src/ml/hybrid_pipeline.py        # rule_score + ml_score combination
src/ml/realtime_hybrid.py        # real-time scoring loop
src/ml/feedback_retraining.py    # assisted retraining with promotion gate
src/dashboard/pages_impl.py      # Streamlit operator dashboard
src/monitoring/metrics.py        # Prometheus logmonitor_* metrics
```

---

## SQL Rules

| Rule | Alert type | Criterion |
|---|---|---|
| Brute Force | `brute_force` | repeated login failures by IP in a time window |
| SQL Injection | `sql_injection` | patterns such as `UNION SELECT`, `OR 1=1`, `DROP TABLE` |
| Port Scanning | `port_scanning` | many distinct endpoints by IP |
| Path Traversal | `path_traversal` | patterns such as `../`, `/etc/passwd`, `/proc/` |
| Suspicious User Agent | `suspicious_user_agent` | tools such as sqlmap, nikto, nmap, masscan |
| Time-Based Anomaly | `time_anomaly` | suspicious activity outside expected hours |

Rules are kept because known security patterns should be fast, auditable and easy to justify.

---

## Machine Learning

The system uses a hybrid approach:

| Model | Role | Reported result |
|---|---|---|
| Isolation Forest | novelty/anomaly detection | F1 0.838, Precision@1% 0.941, ROC-AUC 0.950 |
| Random Forest | supervised component for known patterns | F1 0.783 on temporal holdout |

The final score combines rule-based evidence with ML evidence and maps results into severities such as `NORMAL`, `MEDIUM`, `HIGH` and `CRITICAL`.

CICIDS-2017 is treated as an external benchmark, not as a replacement for operational validation with real logs.

---

## MLOps and Model Governance

The project uses MLOps because the objective is to operate the model lifecycle, not only to train an offline classifier.

Implemented capabilities:

- experiment and artifact tracking with MLflow;
- operational metrics through Prometheus/Grafana;
- analyst dashboard;
- human feedback on alerts;
- assisted retraining from reviewed feedback;
- model promotion gate;
- no silent automatic replacement of the active model.

Retraining is deliberately assisted: the system produces a candidate model and evidence, but final promotion should remain a human decision.

---

## Explainability

The project includes explainability to support auditability and analyst review:

- SHAP as the baseline explainability mechanism;
- LIME for local explanations of the Random Forest component;
- counterfactuals to answer what would need to change to alter a decision;
- dashboard integration for analyst-facing review.

These techniques support transparency and auditability. They are not, by themselves, a legal certification of GDPR or AI Act compliance.

---

## Human Feedback and Incidents

Analysts can mark alerts as:

```text
true_positive
false_positive
false_negative
```

The system also supports an incident workflow:

```text
NEW -> INVESTIGATING -> RESOLVED
```

Incident changes are persisted in an auditable history table and exposed through Prometheus metrics by status.

---

## Observability

Prometheus collects `logmonitor_*` metrics exposed at `/metrics`, covering:

- pipeline throughput and runtime;
- active and cumulative alerts;
- ML performance;
- retraining and candidate promotion state;
- data freshness;
- incident status.

Grafana is auto-provisioned with operational, ML and security dashboards.

Defined SLOs:

| SLO | Objective |
|---|---|
| Availability | >= 99.5% |
| p95 latency | < 200 ms |
| Model F1 | >= 0.75 |
| Data freshness | < 5 min |

---

## Validation

| Validation | Result |
|---|---|
| Real benign logs | 8 lines, 0 alerts, average score 0.3229 |
| Controlled malicious real logs | 21 lines, 8 alerts, average score 0.7035 |
| CICIDS-2017 | average ensemble F1 0.7655 across 5 valid files |
| Load test | p99 25 ms with 50 users |
| Automated tests | 208 passed |
| Coverage | 86.00% |
| Pylint | 9.18/10 |

Explicit limitations:

- CICIDS-2017 is not real operational validation;
- perfect F1 on some subsets does not prove universal generalization;
- retraining does not automatically promote models at runtime;
- public live cloud deployment is future work;
- real-log validation uses small controlled samples.

---

## Useful Commands

Generate normal traffic:

```bash
./venv/bin/python src/flask_app/traffic_generator.py \
  --mode normal \
  --duration 15 \
  --verbose
```

Generate controlled attack traffic:

```bash
./venv/bin/python src/flask_app/traffic_generator.py \
  --mode attack \
  --type sql_injection \
  --num-requests 20 \
  --verbose
```

Import Apache/Nginx logs:

```bash
./venv/bin/python src/log_processor/ingester.py logs/real/access_benign.log \
  --format apache_combined \
  --batch-size 500
```

Offline backfill with `COPY`:

```bash
./venv/bin/python src/log_processor/ingester.py path/to/file.log \
  --format nginx_combined \
  --batch-size 5000 \
  --insert-method copy
```

Run rules in historical mode:

```bash
./venv/bin/python src/detection/rule_engine.py --mode historical --days 1
```

Query alerts:

```bash
docker compose -f docker/docker-compose.yml exec postgres \
  psql -U postgres -d logmonitor -c \
  "SELECT id, alert_type, severity, status, created_at FROM alerts ORDER BY id DESC LIMIT 10;"
```

Query hybrid scores:

```bash
docker compose -f docker/docker-compose.yml exec postgres \
  psql -U postgres -d logmonitor -c \
  "SELECT raw_log_id, rule_score, ml_score, final_score, severity, is_anomaly FROM hybrid_scores ORDER BY raw_log_id DESC LIMIT 10;"
```

Run the main test suite:

```bash
./venv/bin/python -m pytest tests/unit tests/test_flask_app.py tests/test_dashboard.py tests/test_mlflow.py -q
```

---

## CI and Security

GitHub Actions runs quality and security checks on push:

```text
Pylint
Bandit
pytest + coverage
Docker build
Trivy scan
```

Security measures:

- rate limiting with Flask-Limiter;
- input validation with Pydantic;
- security headers;
- non-root containers;
- secrets through environment variables;
- Bandit and Trivy in the quality flow;
- fail-fast schema checks;
- explicit boundary for ML artifact loading.

---

## Cloud POC

A cloud proof-of-concept is prepared, without claiming a completed public live deployment:

```text
docker/docker-compose.cloud-poc.yml
scripts/cloud_poc_smoke_test.sh
scripts/capture_cloud_poc_evidence.sh
```

Public deployment with domain, final TLS setup and automatic CD remains future work.

---

## Project Structure

```text
log-monitor-mlops/
|-- .github/workflows/ci.yml
|-- alembic/
|-- docker/
|   |-- docker-compose.yml
|   |-- docker-compose.cloud-poc.yml
|   |-- Dockerfile.flask
|   |-- Dockerfile.dashboard
|   |-- Dockerfile.ingester
|   |-- Dockerfile.ml-pipeline
|   |-- Dockerfile.rule-engine
|   `-- grafana/
|-- logs/
|   `-- real/
|-- models/
|-- scripts/
|-- src/
|   |-- detection/
|   |-- flask_app/
|   |-- log_processor/
|   |-- ml/
|   |-- monitoring/
|   `-- dashboard/
|-- tests/
|-- requirements.txt
`-- requirements-dev.txt
```

---

## Author

Raul Brito - A22309632
Licenciatura em Engenharia Informatica e Aplicacoes
IPLUSO - Escola Superior de Engenharia e Tecnologias
Academic year 2025/2026 - Supervisor: Acacio Carmona
