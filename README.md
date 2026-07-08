# Log Monitor MLOps

[![CI Pipeline](https://github.com/RaulBrito04/log-monitor-mlops/actions/workflows/ci.yml/badge.svg)](https://github.com/RaulBrito04/log-monitor-mlops/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-70.97%25-yellow)](https://github.com/RaulBrito04/log-monitor-mlops/actions)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![Docker](https://img.shields.io/badge/docker-compose-2496ED.svg?logo=docker)](docker/docker-compose.yml)
[![Security: Bandit](https://img.shields.io/badge/bandit-0%20HIGH%2F0%20MEDIUM-brightgreen.svg)](https://github.com/PyCQA/bandit)

Sistema hibrido open-source de deteccao de anomalias em logs de aplicacoes web, combinando regras SQL deterministicas, machine learning hibrido, feedback humano e explicabilidade com SHAP, LIME e counterfactuals. Alternativa viavel a SIEMs comerciais (EUR10k-EUR100k/ano) para PMEs e contexto academico.

---

## Estado atual

**Goal D fechado nesta fase com validacao operacional, feedback loop e fecho de entrega.**

| Componente | Estado | Evidencia principal |
|---|---|---|
| Ingestao online | Operacional | JSON + Apache/Nginx/Web JSON |
| Bulk/backfill | Operacional | `--insert-method copy` para cargas offline |
| Rule Engine SQL | Operacional | 6 regras + deduplicacao + disciplina de migrations |
| ML hibrido | Operacional | IF + RF + retraining assistido com feedback |
| Explicabilidade | Operacional | SHAP + LIME + counterfactuals locais |
| Dashboard operador | Operacional | Alertas, incidente, feedback, explicacoes |
| API contratual | Operacional | `/openapi.json` + `/docs/api` |
| Monitorizacao | Operacional | Prometheus + Grafana + Alertmanager + metricas ML |
| Qualidade final | Verificada | `183` testes unit/app + `2` integracao + Alembic + Compose |

## Arranque rápido

```bash
# Clonar
git clone https://github.com/RaulBrito04/log-monitor-mlops.git
cd log-monitor-mlops

# Configurar variáveis de ambiente
cp docker/.env.example docker/.env
# editar docker/.env com FLASK_SECRET_KEY e credenciais

# Arrancar o sistema completo (10 containers)
docker compose -f docker/docker-compose.yml up -d

# Verificar estado
docker compose -f docker/docker-compose.yml ps

# Verificar migrations aplicadas
./venv/bin/alembic current
```
**Nota:** o servico `db-migrate` e um job one-shot; aplica as migrations Alembic e termina com codigo `0` por design.

**Gate final local:**

```bash
bash scripts/run_final_quality_gate.sh
```


**Interfaces disponíveis após arranque:**

| Serviço | URL |
|---|---|
| Flask app | http://localhost:5001 |
| API docs | http://localhost:5001/docs/api |
| Streamlit dashboard | http://localhost:8501 |
| Grafana | http://localhost:3000 |
| Prometheus | http://localhost:9090 |
| Alertmanager | http://localhost:9093 |
| MLflow | http://localhost:5000 |

---

## Arquitectura

10 microserviços em 5 camadas, orquestrados com Docker Compose:

```
┌─────────────────────────────────────────────┐
│  ENTRADA          Flask app + Ingester       │
│                   10.933 logs/s              │
└────────────────────────┬────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────┐
│  ARMAZENAMENTO    PostgreSQL + TimescaleDB   │
│                   5 tabelas · hypertable     │
└─────────────────┬──────────┬────────────────┘
                  │          │
                  ▼          ▼
┌──────────────────┐  ┌──────────────────────┐
│  RULE ENGINE     │  │  ML PIPELINE         │
│  6 regras SQL    │  │  Isolation Forest    │
│  0 FP · <41ms   │  │  + Random Forest     │
└────────┬─────────┘  └──────────┬───────────┘
         │                       │
         └──────────┬────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│  SCORING HÍBRIDO  CRITICAL/HIGH/MEDIUM/      │
│                   NORMAL + SHAP             │
└────────────────────────┬────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────┐
│  OBSERVABILIDADE  Prometheus · Grafana       │
│                   Alertmanager · Streamlit   │
└─────────────────────────────────────────────┘
```

---

## Pipeline ML híbrido

**Isolation Forest** (não-supervisionado — novelty detection)
- Treinado exclusivamente com tráfego normal
- Deteta ataques nunca vistos (zero-days) por desvio do baseline
- F1=0,838 · Precision@1%=0,941 · ROC-AUC=0,950

**Random Forest** (supervisionado — padrões conhecidos)
- F1=0,783 em holdout temporal

**Ablation study** confirma que o ensemble hibrido supera qualquer componente isolado em F1-score no dataset interno.

**MLflow** regista cada run de treino: parametros, metricas, artefactos - auditabilidade completa do modelo.

**Human-in-the-loop** ja suporta recolha de feedback no dashboard/API e geracao de dataset revisto para retraining assistido.

---

## Regras de deteção

| Regra | Critério | Latência |
|---|---|---|
| Brute Force | ≥5 falhas de login por IP/5min | 7,6 ms |
| SQL Injection | padrões `' OR`, `UNION SELECT`, etc. | 12,3 ms |
| Port Scanning | ≥10 endpoints distintos por IP/5min | 18,1 ms |
| Path Traversal | padrões `../`, `/etc/passwd` | 9,4 ms |
| Suspicious User Agent | sqlmap, nikto, nmap, masscan | 8,2 ms |
| Time-Based Anomaly | pedidos entre 22h–6h | 40,8 ms |

---

## Segurança

Security hardening aplicado na Semana 14 em toda a stack:

- **Bandit:** 0 findings HIGH · 0 MEDIUM na superfície Flask e ML
- **Trivy:** container scanning automático no CI para todas as imagens
- **Dependabot:** gestão automática de dependências com CVEs
- **Non-root:** 5 containers de runtime executam como `appuser`
- **Rate limiting:** activo em `/login`, `/admin`, `/search`, `/api/upload`
- **Validação de inputs:** Pydantic em todos os endpoints de ingestão
- **Secrets:** variáveis de ambiente — nunca em código
- **HTTP security headers:** aplicados centralmente via middleware
- **Pickle trust boundary:** `_safe_load_pickle` impede carregamento de artefactos externos
- **54 testes de segurança** automatizados a verde

Conformidade por design: GDPR Art. 22 (SHAP), AI Act 2024 (auditabilidade MLflow), NIS2 (logging de eventos de segurança), OWASP Top 10.

---

## Monitorização

**Prometheus** recolhe 9 métricas custom `logmonitor_*` a cada 15 segundos.

**Grafana** provisionado automaticamente no arranque com 3 dashboards:
- *System Overview* — ingestão, latência, disponibilidade
- *ML Performance* — F1, predições por severidade, drift
- *Security Alerts* — alertas por tipo, rate limit hits, blocked auth

**SLOs definidos:**
- Disponibilidade ≥ 99,5%
- Latência p95 < 200 ms
- F1 do modelo ≥ 0,75
- Data freshness < 5 min

**Alertmanager** dispara alertas quando SLOs são violados — sem intervenção humana.

---

## CI/CD

Pipeline GitHub Actions em `.github/workflows/ci.yml`, executa em cada push:

```
Code Quality  →  Pylint (≥7,0/10) + Bandit (0 HIGH/MEDIUM)
Tests         →  pytest + cobertura ≥70%
Build         →  5 imagens Docker + Trivy scan (CRITICAL/HIGH)
Summary       →  relatório no GitHub
```

---

## Stack tecnologico

| Camada | Tecnologias |
|---|---|
| Aplicacao | Python 3.12, Flask, Pydantic, Flask-Limiter |
| Armazenamento | PostgreSQL 16 / TimescaleDB / SQLAlchemy / Alembic |
| ML | Scikit-learn, Isolation Forest, Random Forest, SHAP, LIME |
| MLOps | MLflow, Joblib |
| Containers | Docker, Docker Compose, multi-stage builds |
| Monitorizacao | Prometheus, Grafana, Alertmanager |
| Dashboard | Streamlit |
| Contrato API | OpenAPI 3.1, Swagger UI |
| CI/CD | GitHub Actions, Bandit, Pylint, Trivy, Dependabot |
| Testes | pytest, Locust (load testing) |

## Evidencia de entrega

- Indice consolidado: `docs/FINAL_EVIDENCE_INDEX.md`
- Gate final e reprodutibilidade: `docs/FINAL_QUALITY_REPRO.md`
- Contrato da API: `docs/API_CONTRACT.md`
- Counterfactuals: `docs/COUNTERFACTUALS.md`
- Bulk ingestion: `docs/BULK_INGESTION_MODE.md`

## Estrutura do projeto

```
log-monitor-mlops/
├── .github/workflows/ci.yml       # Pipeline CI/CD
├── docker/
│   ├── docker-compose.yml         # Orquestração dos 10 containers
│   ├── Dockerfile.flask           # multi-stage · non-root
│   ├── Dockerfile.dashboard
│   ├── Dockerfile.ingester
│   ├── Dockerfile.ml-pipeline
│   ├── Dockerfile.rule-engine
│   └── grafana/provisioning/      # Dashboards e datasources como código
├── src/
│   ├── flask_app/
│   │   ├── app.py                 # Flask app + rotas
│   │   ├── config.py              # Configuração centralizada
│   │   ├── security.py            # Security headers
│   │   ├── limiter.py             # Rate limiting
│   │   └── validators.py          # Validação de inputs
│   ├── detection/
│   │   └── rule_engine.py         # 6 regras SQL
│   ├── ml/
│   │   ├── feature_engineering.py # 10 features otimizadas
│   │   ├── train_model.py         # Treino IF + RF + MLflow
│   │   └── hybrid_pipeline.py     # Scoring híbrido + pickle trust boundary
│   └── monitoring/
│       └── metrics.py             # Métricas Prometheus custom
├── tests/
│   ├── unit/
│   │   └── test_security.py       # 54 testes de segurança
│   └── test_flask_app.py
├── experiments/                   # Outputs SHAP (não em produção)
├── models/                        # Artefactos ML versionados
├── docs/
│   ├── GLOSSARIO_TECNICO.md       # Glossário de termos do projeto
│   └── security/
│       └── bandit_exceptions.md   # Justificação de falsos positivos
├── .bandit.yaml                   # Configuração Bandit
├── requirements.txt
└── requirements-dev.txt
```

---

## Resultados de performance

| Métrica | Valor |
|---|---|
| Ingestão | 10.933 logs/s · 39M logs/hora |
| Load test | 50 utilizadores simultâneos · 0 falhas · latência mediana 3ms |
| Rule Engine | 6–41 ms por regra · 0 falsos positivos |
| Isolation Forest F1 | 0,838 |
| Isolation Forest Precision@1% | 0,941 |
| Random Forest F1 | 0,783 |
| ROC-AUC | 0,950 |
| Cobertura de testes | 70,97% |
| Testes de segurança | 54 passam |
| Pylint | 9,18/10 |

---

## Roadmap — Goal D (Semanas 17–23)

| Sprint | Entregável |
|---|---|
| S17 · Mai | Human-in-loop: UI de marcação de FP/FN no Streamlit |
| S18 · Mai | Human-in-loop: pipeline de retraining com feedback acumulado |
| S19 · Jun | Benchmark com dataset CICIDS-2017 |
| S20 · Jun | LIME — explicabilidade complementar ao SHAP |
| S21–S22 · Jun | Incident workflow: NEW → INVESTIGATING → RESOLVED |
| S23 · Jul | Buffer · polimento · documentação final |

**Milestone:** entrega do relatório final em 11 Jul 2026 · discussão em 01 Ago 2026.

---

## Autor

**Raúl Brito** · A22309632  
Licenciatura em Engenharia Informática e Aplicações  
IPLUSO — Escola Superior de Engenharia e Tecnologias  
Ano letivo 2025/2026 · Orientador: Acácio Carmona
