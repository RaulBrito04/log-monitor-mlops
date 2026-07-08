# Resumo Consolidado do Projeto

## Objetivo deste ficheiro
Este documento condensa a informação principal dos documentos de suporte do projeto, em especial as semanas 16 a 23, sem substituir os artefactos originais. O foco é preservar o que é relevante para:

- arquitetura e stack
- pipeline de ingestão, deteção e MLOps
- validação com logs reais e benchmark externo
- explicabilidade
- workflow operacional
- qualidade, reprodutibilidade e evidência final

## Leitura rápida
- Goals A e B ficaram materialmente fechados com ingestão, regras SQL, ML, SHAP, Docker, monitorização, dashboard, testes e CI.
- Goal C fechou o hardening, a preparação de demo e a documentação intermédia.
- Goal D foi concretizado na prática com human-in-the-loop, retraining assistido, validação com logs reais, benchmark complementar, LIME, incident workflow, disciplina de migrações, contrato API, counterfactuals e gate final de qualidade.

## Estado consolidado do sistema

### Arquitetura e operação
- O projeto usa uma arquitetura de microserviços em Docker Compose.
- A base de dados principal é PostgreSQL com TimescaleDB para dados temporais.
- O sistema processa logs, gera alertas determinísticos por regras SQL, calcula scoring híbrido com ML e expõe métricas operacionais por Prometheus.
- A observabilidade inclui Prometheus, Grafana, Alertmanager, MLflow e dashboard Streamlit.

### Tipos de logs suportados
- Logs sintéticos da aplicação Flask durante o desenvolvimento.
- Logs reais gerados por Nginx no protocolo de validação operacional.
- Suporte de ingestão para:
- `json`
- `web_json`
- `apache_combined`
- `apache_common`
- `nginx_combined`
- `auto`

### Valor arquitetural dos docs de glossário e Q&A
Os documentos [GLOSSARIO_TECNICO.md](/home/raulb/projects/log-monitor-mlops/docs/GLOSSARIO_TECNICO.md) e [JURY_QA.md](/home/raulb/projects/log-monitor-mlops/docs/JURY_QA.md) não são evidência de execução, mas ajudam a enquadrar:

- microserviços e multi-stage Docker builds
- PostgreSQL vs TimescaleDB
- diferença entre CI e CD
- Bandit vs Trivy
- path traversal, non-root containers, rate limiting e Pydantic
- escolhas de ML, SHAP, LIME, benchmark e posicionamento face a SIEMs de mercado

## Human-in-the-loop e retraining seguro

### O que foi implementado
Segundo [WEEK16_HUMAN_IN_THE_LOOP.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK16_HUMAN_IN_THE_LOOP.md) e [WEEK16_PROGRESS.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK16_PROGRESS.md):

- o dashboard permite marcar alertas como `true_positive`, `false_positive` e `false_negative`
- o feedback segue para Flask por API, em vez de escrita direta na BD
- existe o endpoint validado `POST /api/alerts/feedback`
- o feedback fica persistido e visível no histórico por alerta
- foi implementado um builder de dataset revisto
- foi implementado retraining assistido de um candidato `RandomForest`
- a promoção é explícita e recomendatória, não automática
- os resultados e checks são publicados em MLflow e artefactos JSON

### Resultado validado
Conforme [WEEK16_RETRAINING_EVIDENCE.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK16_RETRAINING_EVIDENCE.md):

- estado do report: `candidate_built`
- candidato: `promotable = true`
- eventos de feedback elegíveis: `106`
- amostras prontas para treino: `1328`
- split:
- `train`: `796` rows, `199` positivos
- `reviewed_holdout`: `266` rows, `67` positivos
- `temporal_holdout`: `266` rows, `21` positivos

### Métricas operacionais publicadas
- `logmonitor_ml_retraining_promotable = 1`
- `logmonitor_ml_retraining_feedback_events = 106`
- `logmonitor_ml_retraining_ready_log_samples = 1328`
- `logmonitor_ml_retraining_reviewed_f1_delta = 0.9336`
- `logmonitor_ml_retraining_temporal_f1_delta = 0.8537`
- `logmonitor_ml_retraining_temporal_precision_delta = 0.9211`

### Limitações explicitadas
- `false_negative` ainda não é usado como alvo supervisionado direto
- o runtime principal não troca automaticamente para o candidato treinado
- o pipeline continua seguro: sem overwrite silencioso do modelo ativo

## Workflow operacional de incidentes

### O que foi implementado
Ainda em [WEEK16_PROGRESS.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK16_PROGRESS.md):

- ciclo de vida `NEW -> INVESTIGATING -> RESOLVED`
- endpoint `POST /api/alerts/incident`
- campos de incidente em `alerts`
- tabela de auditoria `alert_incident_history`
- métrica Prometheus por estado de incidente
- histórico de transições no dashboard

### Validação funcional
- houve transições reais com sucesso de `NEW` para `INVESTIGATING`
- houve transições reais com sucesso de `INVESTIGATING` para `RESOLVED`
- a linha de auditoria foi escrita na tabela de histórico

## Validação com logs reais

### Contexto
Segundo [REAL_LOGS_VALIDATION_RESULTS.md](/home/raulb/projects/log-monitor-mlops/docs/REAL_LOGS_VALIDATION_RESULTS.md), a validação operacional foi feita com:

- logs reais de access log Nginx
- uma fase benigna
- uma fase maliciosa controlada
- separação clara face ao benchmark externo

### Resultado resumido
- baseline benigno:
- `8` linhas de log reais capturadas
- `8` rows ingeridas em `raw_logs`
- `0` alertas
- `8` hybrid scores
- tráfego malicioso controlado:
- `21` linhas de log reais capturadas
- `21` rows ingeridas em `raw_logs`
- `8` alertas
- `21` hybrid scores

### Tipos de alertas observados na fase maliciosa
- `suspicious_user_agent`
- `path_traversal`
- `brute_force`
- `port_scanning`
- `sql_injection`

### Conclusão prática
- a pipeline operacional funciona ponta a ponta com uma fonte real de logs web
- a validação prova ingestão, persistência, regras, scoring e separação entre baseline benigno e cenário malicioso
- esta validação não foi apresentada como substituto do benchmark externo

## Benchmark externo com CICIDS-2017

### Método
Conforme [CICIDS_BENCHMARK_METHOD.md](/home/raulb/projects/log-monitor-mlops/docs/CICIDS_BENCHMARK_METHOD.md) e [WEEK17_CICIDS_BENCHMARK.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK17_CICIDS_BENCHMARK.md):

- benchmark por ficheiro CSV, não concatenação total
- normalização binária `BENIGN` vs ataque
- split temporal `train / validation / test`
- cap de recursos:
- `60000` rows no treino
- `40000` rows em validation
- `40000` rows em test
- `IsolationForest` treinado só com benignos no treino
- `RandomForest` supervisionado no mesmo split
- ensemble simples sobre os dois sinais

### Resultado consolidado
Segundo [CICIDS_BENCHMARK_RESULTS.md](/home/raulb/projects/log-monitor-mlops/docs/CICIDS_BENCHMARK_RESULTS.md):

- `5` ficheiros benchmarkados com sucesso
- `3` ficheiros excluídos pelo próprio protocolo temporal
- média F1 do ensemble nos runs válidos: `0.7655`
- média F1 do Isolation Forest nos runs válidos: `0.5896`
- melhor caso do ensemble: `1.0000`
- melhor caso do Isolation Forest: `1.0000`

### Interpretação correta
- o benchmark é externo e flow-based
- não substitui a validação com logs reais Nginx/Apache
- alguns resultados perfeitos precisam de contexto
- um caso como `Friday-WorkingHours-Morning` mostrou fraca generalização do `IsolationForest`, o que é relevante e honesto para discussão crítica

## Explicabilidade

### SHAP
- já existia como mecanismo principal de explicabilidade por alerta
- continua a ser a base mais forte para a narrativa de conformidade e interpretação do modelo

### LIME
Segundo [WEEK18_LIME.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK18_LIME.md):

- foi implementado um explainer local para o `RandomForest`
- existe painel LIME ao nível do alerta no dashboard
- há fallback para reconstrução por logs próximos quando necessário
- o dashboard mostra estado vazio em vez de falhar se faltarem pacote ou artefactos

### Counterfactuals
Segundo [COUNTERFACTUALS.md](/home/raulb/projects/log-monitor-mlops/docs/COUNTERFACTUALS.md):

- foi implementado `src/ml/counterfactual_explainer.py`
- há suporte partilhado em `src/ml/reference_explainer.py`
- existe painel de counterfactual no dashboard
- o método:
1. carrega o `RandomForest` supervisionado e as features
2. usa dataset de referência
3. resolve a feature row do log selecionado
4. encontra um exemplo próximo da classe oposta
5. altera features por ordem ponderada até inverter a previsão

### Limites assumidos
- LIME e counterfactuals explicam a perspetiva supervisionada do `RandomForest`
- não fingem explicar diretamente o score híbrido completo regra + IF + RF
- os counterfactuals são heurísticos e locais, não recomendações de negócio

## Throughput, escalabilidade e bulk mode

### Plano e diagnóstico
Segundo [WEEK19_THROUGHPUT_PLAN.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK19_THROUGHPUT_PLAN.md):

- o objetivo foi aumentar `logs/s` sem violar a honestidade da claim de tempo real
- o gargalo suspeito era a cadência artificial do loop realtime e o processamento por log
- decidiu-se não substituir o online path por `COPY`

### Melhorias implementadas
Segundo [WEEK19_BRANCH_SUMMARY.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK19_BRANCH_SUMMARY.md):

- instrumentação de throughput
- baseline collector e report
- knobs de runtime para `HYBRID_FETCH_LIMIT`, `HYBRID_POLL_INTERVAL_SEC`, `RULE_ENGINE_INTERVAL_SEC`
- scoring vetorizado em batch
- lookup de regras em batch
- persistência em batch com `execute_values`
- otimização de feature engineering
- otimização do fetch path

### Resultados medidos
Segundo [WEEK19_BASELINE_RESULTS.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK19_BASELINE_RESULTS.md):

- `hybrid_pipeline`: `4025.615 logs/s`
- `realtime_hybrid`: `2286.194 logs/s` em processamento ativo sem sleep
- `ingester`: `1259.708 logs/s`
- configuração do daemon observada no baseline:
- `HYBRID_FETCH_LIMIT=1000`
- `HYBRID_POLL_INTERVAL_SEC=2`
- ceiling configurado: `500 logs/s`

### Interpretação importante
- o benchmark mede throughput ativo de processamento
- o daemon live continua limitado por `fetch_limit / poll_interval`
- por isso, throughput medido e throughput sustentável em steady state não são exatamente a mesma coisa

### Bulk ingestion
Segundo [BULK_INGESTION_MODE.md](/home/raulb/projects/log-monitor-mlops/docs/BULK_INGESTION_MODE.md):

- o modo default online continua a ser `execute_values`
- existe caminho opcional offline com PostgreSQL `COPY`
- uso previsto:
- backfill histórico
- benchmark ingestion
- stress tests
- demo reprodutível com maior volume

## Diversidade de logs e idempotência das regras

### Week 20
Segundo [WEEK20_BRANCH_SUMMARY.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK20_BRANCH_SUMMARY.md):

- a ingestão passou a suportar múltiplos formatos web, incluindo `web_json` e `nginx_combined`
- houve normalização de campos Nginx-style JSON para o schema interno
- os testes unitários do ingester foram alargados
- foi corrigido o problema de duplicação de alertas no rule engine

### Fix de idempotência
- adicionou-se `alerts.dedup_key`
- adicionou-se índice parcial único para alerts de regra
- passou a existir semântica `INSERT ... ON CONFLICT ... DO UPDATE`
- efeito prático:
- se a mesma janela for reprocessada com o mesmo conjunto de logs, o alerta não duplica
- se o conjunto de logs mudar, um novo alerta ainda pode surgir

## Migrações e disciplina de schema

### Week 21
Segundo [WEEK21_BRANCH_SUMMARY.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK21_BRANCH_SUMMARY.md) e [DB_MIGRATIONS.md](/home/raulb/projects/log-monitor-mlops/docs/DB_MIGRATIONS.md):

- o projeto adotou Alembic como source of truth do schema
- `docker/init.sql` deixou de ser a verdade principal
- existe serviço `db-migrate` no Compose
- testes e CI passaram a aplicar `alembic upgrade head`
- a revisão atual validada é `20260707_0001 (head)`

### Week 22
Segundo [WEEK22_BRANCH_SUMMARY.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK22_BRANCH_SUMMARY.md):

- foram removidas mutações runtime ao schema em `rule_engine` e `flask_app`
- foi adicionado `src/db/schema_checks.py`
- o arranque agora falha cedo se a BD não estiver no schema esperado
- foram adicionados testes de contrato dos adaptadores de logs

## API contratual

### O que existe
Segundo [API_CONTRACT.md](/home/raulb/projects/log-monitor-mlops/docs/API_CONTRACT.md):

- OpenAPI JSON em `/openapi.json`
- Swagger UI em `/docs/api`

### Endpoints cobertos
- `GET /health`
- `POST /metrics/ml_quality`
- `POST /login`
- `POST /api/alerts/feedback`
- `POST /api/alerts/incident`
- `GET /api/data`
- `GET /api/users`
- `GET /search`
- `POST /api/upload`
- `GET /admin`

### Valor
- contrato legível por máquinas
- menor drift entre implementação e documentação
- melhor integração externa e melhor demo

## Gate final de qualidade e reprodutibilidade

### O que foi fechado na Week 23
Segundo [WEEK23_BRANCH_SUMMARY.md](/home/raulb/projects/log-monitor-mlops/docs/WEEK23_BRANCH_SUMMARY.md):

- gate final de qualidade
- consolidação de evidência
- counterfactuals
- contrato API OpenAPI/Swagger
- bulk ingestion opcional
- fecho conjunto com a disciplina de schema/adapters da fase anterior

### Resultado verificado
Segundo [FINAL_QUALITY_REPRO.md](/home/raulb/projects/log-monitor-mlops/docs/FINAL_QUALITY_REPRO.md):

- `py_compile` passou
- dataset quality carregado de `data/reviewed_feedback_dataset.csv`
- `183 passed` em unit/app/dashboard/MLflow
- `2 passed` em integração
- Alembic em `20260707_0001 (head)`
- `docker compose -f docker/docker-compose.yml config` passou

### Observações sobre dataset quality
- missing values em `exclude_reason` e `original_dataset_label`
- várias colunas constantes por desenho do artefacto revisto
- positive rate: `21.61%`
- janela temporal do dataset: `2026-03-01` a `2026-07-01`

## Papel do roadmap

### O que o roadmap representa
O [ROADMAP.md](/home/raulb/projects/log-monitor-mlops/docs/ROADMAP.md) é o plano original estruturado por Goals e semanas.

### Como ler hoje
- Goals A e B: concluídos
- Goal C: roadmap ainda marca algumas partes como em curso, mas o sistema já avançou para fechos posteriores
- Goal D: o roadmap descreve o plano de implementação de human-in-loop, logs reais, benchmark, LIME, incident workflow e fecho final
- os docs das Weeks 16 a 23 mostram que essa maioria foi efetivamente materializada

## Mapa de evidência final

### Documento agregador
O índice principal de prova é [FINAL_EVIDENCE_INDEX.md](/home/raulb/projects/log-monitor-mlops/docs/FINAL_EVIDENCE_INDEX.md).

### Ordem recomendada para demo ou defesa
1. arquitetura e stack
2. validação com logs reais
3. dashboard, alertas e feedback humano
4. retraining e MLflow
5. SHAP, LIME e counterfactuals
6. monitorização, CI, migrações e throughput

## Limitações e honestidade técnica
- benchmark externo não substitui validação operacional real
- resultados perfeitos em alguns ficheiros do CICIDS-2017 não devem ser tratados como prova universal de generalização
- LIME e counterfactuals explicam o `RandomForest`, não o score híbrido inteiro
- bulk mode não substitui o caminho online
- a promoção de modelo é assistida e explícita, não automática
- logs reais usados foram reais mas controlados, não tráfego espontâneo de produção

## Conclusão consolidada
O projeto terminou com uma stack coerente e demonstrável de deteção híbrida para logs web, combinando:

- ingestão multi-formato
- regras SQL com idempotência
- ML híbrido com feedback humano e retraining seguro
- validação operacional com logs reais
- benchmark externo complementar
- explicabilidade por SHAP, LIME e counterfactuals
- workflow de incidente auditável
- observabilidade, CI e disciplina de migrações
- gate final de qualidade e reprodutibilidade

Para leitura detalhada ou defesa técnica, os documentos originais continuam a ser a fonte principal de contexto e prova.
