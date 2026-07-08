# Dossier Completo Pos-Intermedio

## 1. Objetivo do dossier

Este documento agrega de forma organizada a informacao principal produzida apos o relatorio intermedio, cobrindo sobretudo as semanas 16 a 23 e os respetivos fechos tecnicos. O objetivo e funcionar como dossier unico de consulta para:

- estado real do projeto apos o intermedio
- comparacao entre roadmap e implementacao efetiva
- funcionalidades implementadas
- validacoes executadas
- resultados e metricas
- limitacoes assumidas explicitamente
- evidencias relevantes para relatorio, demo e defesa

Este ficheiro nao substitui os documentos originais. Serve como consolidacao completa de alto detalhe.

## 2. Fontes usadas neste dossier

Este dossier foi consolidado a partir de:

- `docs/ROADMAP.md`
- `docs/WEEK16_HUMAN_IN_THE_LOOP.md`
- `docs/WEEK16_PROGRESS.md`
- `docs/WEEK16_RETRAINING_EVIDENCE.md`
- `docs/REAL_LOGS_VALIDATION_RESULTS.md`
- `docs/REAL_LOGS_VALIDATION_STEPS.md`
- `docs/WEEK17_CICIDS_BENCHMARK.md`
- `docs/CICIDS_BENCHMARK_METHOD.md`
- `docs/CICIDS_BENCHMARK_RESULTS.md`
- `docs/WEEK18_LIME.md`
- `docs/WEEK19_THROUGHPUT_PLAN.md`
- `docs/WEEK19_BASELINE_RESULTS.md`
- `docs/WEEK19_BRANCH_SUMMARY.md`
- `docs/WEEK20_BRANCH_SUMMARY.md`
- `docs/WEEK21_BRANCH_SUMMARY.md`
- `docs/DB_MIGRATIONS.md`
- `docs/WEEK22_BRANCH_SUMMARY.md`
- `docs/API_CONTRACT.md`
- `docs/COUNTERFACTUALS.md`
- `docs/FINAL_EVIDENCE_INDEX.md`
- `docs/FINAL_QUALITY_REPRO.md`
- `docs/WEEK23_BRANCH_SUMMARY.md`
- `docs/BULK_INGESTION_MODE.md`
- `docs/GLOSSARIO_TECNICO.md`
- `docs/JURY_QA.md`

## 3. Contexto geral apos o relatorio intermedio

### 3.1 Estado de alto nivel

No pos-intermedio, o projeto deixou de estar focado apenas em:

- deteccao base por regras
- pipeline ML inicial
- dashboard e monitorizacao base
- Docker, testes e CI

e passou a fechar temas mais proximos de um sistema operacional e apresentavel:

- human-in-the-loop
- retraining assistido com evidencias
- validacao com logs reais
- benchmark externo complementar
- LIME e counterfactuals
- workflow de incidente
- disciplina de schema e migracoes
- contrato API
- gate final de qualidade e reprodutibilidade
- modo bulk separado para throughput offline

### 3.2 Visao consolidada do sistema

O sistema atual, considerando o material pos-intermedio, inclui:

- ingestao de logs multi-formato
- armazenamento em PostgreSQL/TimescaleDB
- regras SQL deterministicas
- scoring hibrido com `IsolationForest` e suporte supervisionado em `RandomForest`
- explicabilidade com SHAP, LIME e counterfactuals locais
- dashboard Streamlit para operador
- Prometheus, Grafana, Alertmanager e MLflow
- feedback humano e retraining assistido
- workflow de incidente com auditoria
- CI com testes, quality checks e build validation

## 4. Roadmap vs implementacao real

### 4.1 Leitura do roadmap

Segundo `docs/ROADMAP.md`, a fase pos-intermedia centra-se sobretudo em Goal D:

- S17: human-in-loop feedback UI
- S18: retraining com feedback
- S19: validacao com logs reais + benchmark externo
- S20: LIME
- S21-S22: incident workflow
- S23: polimento, reproducao e documentacao final

### 4.2 Estado real por bloco

| Bloco | Planeado no roadmap | Estado real consolidado |
|---|---|---|
| S17 feedback UI | Sim | Implementado |
| S18 retraining assistido | Sim | Implementado e validado com promocao explicita |
| S19 logs reais | Sim | Implementado e validado ponta a ponta |
| S19 benchmark CICIDS | Sim | Implementado com protocolo e exclusoes explicitas |
| S20 LIME | Sim | Implementado |
| S21-S22 incident workflow | Sim | Implementado com auditoria e metricas |
| S23 qualidade/reproducao | Sim | Implementado com gate final |

### 4.3 Diferenca entre plano e estado efetivo

O roadmap ainda contem alvos e wording de planeamento, mas a documentacao das semanas posteriores mostra que a maior parte do Goal D foi efetivamente fechada. No entanto, ha pontos que devem continuar a ser lidos com honestidade:

- o roadmap apontava para cobertura `>=85%`, mas os docs finais aqui consolidados validam sobretudo testes a passar, nao uma nova percentagem final de coverage comprovada neste dossier
- o roadmap colocava cloud POC em pos-entrega, e este dossier nao contem evidencia de cloud deployment concluido
- o roadmap falava em "API publica" em sentido amplo; o que esta efetivamente validado aqui e um contrato OpenAPI/Swagger sobre a superficie Flask existente

## 5. Human-in-the-loop e retraining seguro

### 5.1 Captura de feedback humano

Segundo `docs/WEEK16_HUMAN_IN_THE_LOOP.md`, ficou implementado:

- marcacao de alertas como `true_positive`, `false_positive` e `false_negative`
- notas do analista por alerta
- envio do feedback via Flask
- endpoint `POST /api/alerts/feedback`
- rate limiting nesse endpoint
- persistencia na tabela `feedback`
- visualizacao do historico de feedback no dashboard

Isto fecha a parte de recolha de feedback humano. O sistema passa a ter labels revistas por analista, em vez de depender apenas de labels sinteticas ou heuristicas de desenvolvimento.

### 5.2 Porque o passo seguinte nao era "retreinar logo"

O proprio doc explica corretamente que retreinar diretamente a partir de rows de feedback seria inseguro. O fluxo responsavel definido foi:

1. cruzar feedback com alertas e `log_ids`
2. mapear labels revistas para alvos treinaveis
3. reutilizar artefactos de features sempre que possivel
4. construir dataset revisto
5. dividir em `train`, `reviewed_holdout` e `temporal_holdout`
6. treinar candidato
7. comparar com baseline em MLflow
8. aplicar logica explicita de promocao

### 5.3 Componentes implementados

Foram implementados tres blocos principais:

- reviewed dataset builder
- candidate retraining job
- promotion gate + report em MLflow/JSON

Comportamento importante:

- `false_negative` continua visivel no artefacto, mas nao e ainda alvo supervisionado direto
- o candidato `RandomForest` e guardado separadamente
- nao existe overwrite silencioso do modelo ativo

### 5.4 Artefactos esperados

O fluxo passa a produzir artefactos como:

- `data/reviewed_feedback_dataset.csv`
- `data/reviewed_feedback_dataset.pkl`
- `data/reviewed_feedback_dataset_iforest.csv`
- `data/reviewed_feedback_dataset_iforest.pkl`
- `data/reviewed_feedback_summary.json`
- `models/random_forest_feedback_candidate.pkl`
- `models/random_forest_feedback_candidate_metadata.json`
- `experiments/feedback_retraining_report.json`

### 5.5 Primeira iteracao validada

Comandos registados:

```bash
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.seed_reviewed_feedback --max-positive-alerts 40 --negative-ratio 2.0 --dry-run
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.seed_reviewed_feedback --max-positive-alerts 40 --negative-ratio 2.0
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.feedback_retraining
```

Resultado da primeira iteracao:

- seed inserida: `120` feedback rows
- `40` `true_positive`
- `80` `false_positive`
- eventos elegiveis: `103`
- samples prontas para treino: `1307`
- split temporal inicial sem positivos no holdout temporal
- baseline reviewed holdout F1: `0.0156`
- candidate reviewed holdout F1: `0.9710`
- promocao: `not promotable`

Interpretacao correta:

- o workflow estava funcional
- o candidato parecia forte no reviewed holdout
- mas a promocao foi corretamente bloqueada por falta de positivos no `temporal_holdout`

### 5.6 Segunda iteracao com evidencias recentes

Para fechar a lacuna temporal, foi feita nova ronda com positivos vindos da validacao real mais recente.

Comandos:

```bash
POSTGRES_PASSWORD=changeme_em_prod REAL_LOG_RESULTS_PATH=docs/WEEK16_RETRAINING_EVIDENCE.md bash scripts/run_real_log_validation.sh
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.seed_validation_feedback --results-path docs/WEEK16_RETRAINING_EVIDENCE.md --summary-path data/validation_feedback_seed_summary.json
POSTGRES_PASSWORD=changeme_em_prod python -m src.ml.feedback_retraining
```

Resultado atualizado:

- novos positivos revistos inseridos: `16`
- eventos elegiveis: `106`
- samples prontas: `1328`
- `train`: `796` rows, `199` positivos
- `reviewed_holdout`: `266` rows, `67` positivos
- `temporal_holdout`: `266` rows, `21` positivos
- baseline reviewed holdout F1: `0.0304`
- candidate reviewed holdout F1: `0.9640`
- baseline temporal holdout F1: `0.1463`
- candidate temporal holdout F1: `1.0000`
- delta temporal F1: `+0.8537`
- delta temporal precision: `+0.9211`
- promocao: `promotable = true`

### 5.7 Metricas operacionais publicadas

Segundo `docs/WEEK16_RETRAINING_EVIDENCE.md` e `docs/WEEK16_PROGRESS.md`:

- `logmonitor_ml_retraining_promotable = 1`
- `logmonitor_ml_retraining_feedback_events = 106`
- `logmonitor_ml_retraining_ready_log_samples = 1328`
- `logmonitor_ml_retraining_reviewed_f1_delta = 0.9336105260278469`
- `logmonitor_ml_retraining_temporal_f1_delta = 0.8536585365853658`
- `logmonitor_ml_retraining_temporal_precision_delta = 0.9210526315789473`

### 5.8 Limitacoes mantidas explicitas

- o runtime em producao nao troca automaticamente para este candidato
- o loop de governao esta implementado, mas a promocao continua assistida
- `false_negative` ainda nao e usado como alvo supervisionado direto
- a evidencia valida um ciclo responsavel de model governance, nao "auto-learning" irrestrito

## 6. Workflow operacional de incidentes

### 6.1 O que foi implementado

Segundo `docs/WEEK16_PROGRESS.md`, ficou implementado:

- ciclo `NEW -> INVESTIGATING -> RESOLVED`
- endpoint `POST /api/alerts/incident`
- campos em `alerts`:
- `incident_status`
- `incident_owner`
- `incident_notes`
- `incident_updated_at`
- `incident_updated_by`
- tabela `alert_incident_history`
- metrica Prometheus `logmonitor_incident_alerts_total{incident_status=...}`
- historico visivel no dashboard

### 6.2 Regras de transicao

Workflow permitido:

- `NEW -> INVESTIGATING`
- `INVESTIGATING -> RESOLVED`
- updates no mesmo estado sao permitidos
- reabertura `RESOLVED -> INVESTIGATING` esta rejeitada nesta versao

### 6.3 Validacao funcional

Evidencia funcional documentada:

- transicao executada com sucesso para `INVESTIGATING`
- transicao executada com sucesso para `RESOLVED`
- auditoria escrita em `alert_incident_history`

Comando de exemplo documentado:

```powershell
$payload = @{ alert_id = 1; incident_status = 'INVESTIGATING'; incident_owner = 'analyst'; incident_notes = 'triaged in week16 MVP validation'; user_id = 'analyst' } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri 'http://localhost:5001/api/alerts/incident' -Body $payload -ContentType 'application/json'
```

## 7. Validacao com logs reais

### 7.1 Porque esta fase era critica

O roadmap e a nota recebida apos a apresentacao intercalar tornaram explicito que benchmark externo nao substitui validacao com logs reais. Por isso, a prova exigida passou a ser:

- fonte real de logs
- baseline benigno
- trafego malicioso controlado
- avaliacao ponta a ponta

### 7.2 Protocolo usado

Segundo `docs/REAL_LOGS_VALIDATION_STEPS.md`, o protocolo recomendado e usado assenta em:

- fonte real Nginx/Apache
- trafego benigno real
- trafego malicioso controlado
- criterios de avaliacao explicitos

### 7.3 Suporte tecnico preparado

A branch de validacao real ja suportava:

- `--format json`
- `--format apache_combined`
- `--format auto`
- fallback para campos em falta
- sanitizacao de `NaN` e `inf` antes do scoring ML

### 7.4 Resultado validado

Segundo `docs/REAL_LOGS_VALIDATION_RESULTS.md`:

- fonte: `Nginx access log`
- proxy URL: `http://localhost:8088`
- benign source IP: `198.51.100.184`
- attack source IP: `203.0.113.184`

Resumo:

- benign:
- `8` linhas reais capturadas
- `8` rows ingeridas em `raw_logs`
- `0` alertas
- `8` hybrid scores

- malicious:
- `21` linhas reais capturadas
- `21` rows ingeridas em `raw_logs`
- `8` alertas
- `21` hybrid scores

### 7.5 Detalhes observados

Baseline benigno:

- sem campos obrigatorios em falta
- `0` alertas
- `0` anomalias ML
- `avg_final_score = 0.3229`
- status codes: apenas `200`

Trafego malicioso controlado:

- sem campos obrigatorios em falta
- alertas:
- `suspicious_user_agent`: `3`
- `path_traversal`: `2`
- `brute_force`: `1`
- `port_scanning`: `1`
- `sql_injection`: `1`
- `21` anomalias ML
- `avg_final_score = 0.7035`
- status codes: `404`, `401`, `200`, `429`

### 7.6 Interpretacao correta

- os logs sao reais
- o trafego malicioso e controlado, nao "espontaneo de producao"
- a validacao prova ingestao, persistencia, regras, scoring e comportamento do dashboard
- esta fase fica separada do benchmark CICIDS

## 8. Benchmark externo com CICIDS-2017

### 8.1 Objetivo

Segundo `docs/WEEK17_CICIDS_BENCHMARK.md`, o objetivo foi adicionar validacao externa da familia de metodos de deteccao sem fingir equivalencia com a pipeline operacional HTTP.

### 8.2 Metodo

Segundo `docs/CICIDS_BENCHMARK_METHOD.md`:

- benchmark por ficheiro CSV
- fallback de encodings `utf-8`, `cp1252`, `latin1`
- normalizacao para benchmark binario `BENIGN` vs ataque
- split temporal `train / validation / test`
- cap:
- `60000` treino
- `40000` validation
- `40000` test
- `IsolationForest` treinado apenas com benignos do treino
- `RandomForest` supervisionado
- thresholds ajustados em validation
- output em JSON e Markdown por ficheiro

### 8.3 Criterios de exclusao

Um ficheiro podia ser excluido se:

- treino nao tivesse mistura suficiente de benign e ataque
- validation/test nao tivessem ataques suficientes
- houvesse falha tecnica de parsing/execucao

### 8.4 Resultado consolidado

Segundo `docs/CICIDS_BENCHMARK_RESULTS.md`:

- `5` ficheiros benchmarkados com sucesso
- `3` ficheiros excluidos pelo proprio protocolo temporal

Resultados por ficheiro:

| File | IF F1 | RF F1 | Ensemble F1 |
|---|---:|---:|---:|
| Friday-WorkingHours-Afternoon-DDos | 0.5318 | 0.9931 | 0.9911 |
| Friday-WorkingHours-Afternoon-PortScan | 0.7942 | 0.9984 | 0.9963 |
| Friday-WorkingHours-Morning | 0.0313 | 0.8198 | 0.0000 |
| Thursday-WorkingHours-Morning-WebAttacks | 1.0000 | 1.0000 | 1.0000 |
| Wednesday-workingHours | 0.5906 | 0.9625 | 0.8398 |

Destaques:

- melhor F1 do ensemble: `1.0000`
- melhor F1 do IF: `1.0000`
- media F1 do ensemble: `0.7655`
- media F1 do IF: `0.5896`

Exclusoes:

- `Monday-WorkingHours.pcap_ISCX.csv`
- `Thursday-WorkingHours-Afternoon-Infilteration.pcap_ISCX.csv`
- `Tuesday-WorkingHours.pcap_ISCX.csv`

### 8.5 Interpretacao correta

- e um benchmark externo flow-based
- nao substitui a validacao operacional com logs reais
- `ROC-AUC = nan` em alguns casos deve ser lido como limitacao do split, nao como prova definitiva
- o caso `Friday-WorkingHours-Morning` mostra que o `IsolationForest` nao generalizou bem em todos os cenarios
- isto reforca a importancia de multiplas validacoes, nao de um unico numero

## 9. Explicabilidade: SHAP, LIME e counterfactuals

### 9.1 SHAP

O projeto ja vinha com SHAP como base de explicabilidade. No pos-intermedio, SHAP manteve-se como mecanismo principal mais forte para:

- interpretacao local por alerta
- narrativa de auditabilidade
- enquadramento de conformidade e transparencia

### 9.2 LIME

Segundo `docs/WEEK18_LIME.md`:

- foi criado `src/ml/lime_explainer.py`
- existe painel LIME no dashboard
- a explicacao incide sobre `random_forest_latest.pkl`
- o dashboard tenta primeiro usar dataset armazenado
- se necessario, cai para reconstrucao local a partir de raw logs

Design choice importante:

- o LIME foi ligado ao `RandomForest`, nao ao score hibrido total
- a UI rotula a explicacao como `random_forest` local explanation

Limitacao:

- LIME continua a explicar a perspetiva supervisionada, nao o score hibrido inteiro

### 9.3 Counterfactuals

Segundo `docs/COUNTERFACTUALS.md`:

- foi adicionado `src/ml/counterfactual_explainer.py`
- foi adicionado suporte comum em `src/ml/reference_explainer.py`
- existe painel de counterfactual no dashboard

Metodo:

1. carregar o `RandomForest` e as features
2. carregar dataset de referencia
3. localizar a feature row do log
4. encontrar exemplo proximo da classe oposta
5. aplicar mudancas de feature por ordem ponderada ate inverter a previsao

Porque e uma implementacao responsavel:

- nao finge explicar o score hibrido completo
- fica ancorada em dados de referencia observados
- assume explicitamente o carater local e heuristico

## 10. Throughput, escalabilidade e modo bulk

### 10.1 Hipotese inicial

Segundo `docs/WEEK19_THROUGHPUT_PLAN.md`, o problema nao parecia ser apenas insert bruto na BD. Os gargalos suspeitos eram:

- teto artificial do loop realtime
- demasiado trabalho por log
- persistencia e lookup demasiado granulares
- possivel contencao se a frequencia do rule engine fosse aumentada sem cuidado

### 10.2 Instrumentacao e baseline

Foram adicionados:

- instrumentacao de ingestao
- instrumentacao do realtime hybrid
- instrumentacao de stages do hybrid pipeline
- baseline collector e report markdown

### 10.3 Melhorias implementadas

Segundo `docs/WEEK19_BRANCH_SUMMARY.md`:

- knobs de runtime:
- `HYBRID_POLL_INTERVAL_SEC`
- `HYBRID_FETCH_LIMIT`
- `RULE_ENGINE_INTERVAL_SEC`
- `RULE_ENGINE_WINDOW`
- persistencia em batch com `execute_values`
- rule lookup em batch
- scoring ML vetorizado em batch
- feature engineering otimizada
- fetch path otimizado

### 10.4 Resultados medidos

Segundo `docs/WEEK19_BASELINE_RESULTS.md`:

- `hybrid_pipeline`: `4025.615 logs/s`
- `realtime_hybrid`: `2286.194 logs/s`
- `ingester`: `1259.708 logs/s`
- fetch: `0.141 s`
- feature engineering: `0.404 s`
- evaluation: `1.637 s`
- `HYBRID_FETCH_LIMIT=1000`
- `HYBRID_POLL_INTERVAL_SEC=2`
- ceiling configurado do daemon: `500 logs/s`

### 10.5 Leitura correta destes numeros

Ponto importante que o proprio doc deixa explicito:

- o collector mede throughput de processamento ativo sem o sleep entre ciclos
- o daemon live continua limitado por `fetch_limit / poll_interval`
- portanto, `2286 logs/s` nao significa automaticamente throughput steady-state sustentado do mesmo loop em producao

### 10.6 Bulk mode

Segundo `docs/BULK_INGESTION_MODE.md`:

- online default: `execute_values`
- modo opcional offline: PostgreSQL `COPY`

Uso previsto:

- backfill historico
- benchmark ingestion
- stress tests
- demos reprodutiveis com maior volume

Posicionamento correto:

- o modo `COPY` nao substitui o online path
- e um caminho separado para throughput offline

## 11. Diversidade de formatos de logs e idempotencia das regras

### 11.1 Objetivo

Segundo `docs/WEEK20_BRANCH_SUMMARY.md`, o objetivo foi:

- suportar mais do que um formato real de access log
- validar a pipeline com esses formatos
- corrigir a duplicacao de alertas quando a mesma janela era reavaliada

### 11.2 Formatos suportados

Atualizacao do ingester para:

- `json`
- `web_json`
- `apache_combined`
- `apache_common`
- `nginx_combined`
- `auto`

O modo `auto` distingue:

- web JSON estruturado
- JSON generico
- linhas classicas de access log

### 11.3 Normalizacao de web JSON

Foram adicionados mapeamentos de campos tipo Nginx JSON, como:

- `remote_addr`
- `request`
- `time_local`
- `time_iso8601`
- `request_time`
- `http_user_agent`
- `body_bytes_sent`

### 11.4 Testes e smoke validation

Testes:

```bash
./venv/bin/python -m py_compile src/detection/rule_engine.py src/log_processor/ingester.py tests/unit/test_rule_engine.py tests/unit/test_ingester.py
./venv/bin/python -m pytest --no-cov tests/unit/test_rule_engine.py tests/unit/test_ingester.py -q
```

Resultado:

- `24 passed`

Smoke validation:

- `7` logs maliciosos `web_json` ingeridos
- `2` logs benignos `nginx_combined` ingeridos
- `9` logs persistidos
- `hybrid_scores` criados
- alertas disparados em `web_json` para:
- `brute_force`
- `path_traversal`
- `sql_injection`
- `suspicious_user_agent`

### 11.5 Fix de duplicacao no rule engine

O problema era a re-insercao do mesmo alerta quando a mesma janela era reprocessada.

Correcao:

- `alerts.dedup_key`
- indice unico parcial `idx_alerts_rule_dedup`
- `INSERT ... ON CONFLICT ... DO UPDATE`
- geracao deterministica de `dedup_key`

Efeito pratico:

- mesma deteccao exata reprocessada -> nao duplica
- conjunto de logs diferente -> pode gerar novo alerta

Validacao real:

- primeira execucao: `1` alerta
- segunda execucao: `0` alertas
- contagem final: `1`

## 12. Migracoes, disciplina de schema e contratos de adaptadores

### 12.1 Week 21: Alembic

Segundo `docs/WEEK21_BRANCH_SUMMARY.md` e `docs/DB_MIGRATIONS.md`:

- foi criado o baseline Alembic
- `docker/init.sql` deixou de ser a verdade principal
- existe `db-migrate` no Docker Compose
- `tests/conftest.py` passa a aplicar `alembic upgrade head`
- GitHub Actions faz smoke check Alembic

Bootstrap esperado:

1. PostgreSQL fica healthy
2. `db-migrate` corre `alembic upgrade head`
3. servicos de runtime esperam pelo fim das migracoes

Validacao:

- `py_compile`: passou
- testes unitarios: `27 passed`
- integracao: `2 passed`
- Alembic current: `20260707_0001 (head)`
- `docker compose config`: passou

### 12.2 Week 22: schema discipline e log adapter contracts

Segundo `docs/WEEK22_BRANCH_SUMMARY.md`:

- deixaram de existir mutacoes runtime escondidas em `rule_engine` e `flask_app`
- foi adicionado `src/db/schema_checks.py`
- o startup agora valida schema e falha cedo se faltar algo
- foram adicionados testes de contrato dos adaptadores de logs

Objetos exigidos:

- `alerts.dedup_key`
- `idx_alerts_rule_dedup`
- colunas de incident workflow em `alerts`
- `alert_incident_history`
- indexes do historico

Validacao:

- `py_compile` passou
- `97 passed` em suite focada
- `2 passed` em integracao
- `16 passed` dashboard
- `docker compose config` passou

## 13. Contrato API

### 13.1 Exposicao

Segundo `docs/API_CONTRACT.md`:

- JSON OpenAPI: `http://localhost:5001/openapi.json`
- Swagger UI: `http://localhost:5001/docs/api`

### 13.2 Endpoints documentados

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

### 13.3 Valor pratico

- contrato legivel por maquinas
- integracao externa mais clara
- menor risco de drift entre implementacao e documentacao

Nota importante:

- o JSON OpenAPI e o artefacto mais forte
- a Swagger UI depende de assets carregados por CDN

## 14. Gate final de qualidade e reprodutibilidade

### 14.1 Objetivo

Segundo `docs/FINAL_QUALITY_REPRO.md`, passou a existir um comando unico para validar estado de entrega:

```bash
bash scripts/run_final_quality_gate.sh
```

### 14.2 O que o gate verifica

1. `py_compile`
2. dataset quality leve
3. testes unit + Flask + dashboard + MLflow
4. integracao
5. Alembic upgrade/current
6. `docker compose config`

### 14.3 Ultima validacao verificada

Segundo o mesmo doc:

- `py_compile` passou
- dataset quality carregado de `data/reviewed_feedback_dataset.csv`
- `183 passed` em unit/app/dashboard/MLflow
- `2 passed` em integracao
- Alembic em `20260707_0001 (head)`
- `docker compose -f docker/docker-compose.yml config` passou

Observacoes ao dataset:

- missing values em `exclude_reason` e `original_dataset_label`
- varias colunas de controlo constantes por desenho
- positive rate: `21.61%`
- time range: `2026-03-01` a `2026-07-01`

### 14.4 Week 23 closeout

Segundo `docs/WEEK23_BRANCH_SUMMARY.md`, o fecho do Goal D incluiu:

- gate final de qualidade
- consolidacao de evidencia
- counterfactuals
- OpenAPI/Swagger
- bulk ingestion opcional
- integracao com a disciplina de schema da fase anterior

## 15. Evidencia final e ordem de uso

### 15.1 Indice principal

O ficheiro `docs/FINAL_EVIDENCE_INDEX.md` funciona como mapa de prova do projeto. Agrupa:

- validacao real
- benchmark CICIDS
- human-in-the-loop e retraining
- explicabilidade
- monitorizacao e plataforma
- throughput
- gate final
- API contract
- guiao/demo e Q&A

### 15.2 Ordem recomendada para apresentacao ou defesa

O proprio indice sugere uma sequencia responsavel:

1. arquitetura e stack
2. validacao com logs reais
3. dashboard, alertas e feedback humano
4. retraining e MLflow
5. SHAP, LIME e counterfactuals
6. monitorizacao, CI/CD, migracoes e throughput

## 16. Papel dos documentos de apoio tecnico

### 16.1 Glossario tecnico

`docs/GLOSSARIO_TECNICO.md` ajuda a explicar e defender conceitos como:

- microservicos
- multi-stage Docker builds
- PostgreSQL vs TimescaleDB
- ACID e time-series
- CI vs CD
- Bandit vs Trivy
- non-root containers
- path traversal
- rate limiting
- Pydantic

Nao e evidencia de execucao, mas e importante para comunicar as decisoes tecnicas com rigor.

### 16.2 Jury Q&A

`docs/JURY_QA.md` agrega material de defesa sobre:

- escolha de `IsolationForest` e `RandomForest`
- SHAP vs LIME
- porque nao usar deep learning nesta fase
- OWASP, hardening e secrets
- Flask vs FastAPI
- PostgreSQL/TimescaleDB vs outras opcoes
- Docker Compose vs Kubernetes
- interpretacao de F1, throughput e cobertura
- mercado e benchmarking face a SIEMs
- enquadramento regulatorio

Tambem nao e evidencia operacional direta, mas e relevante para defesa oral.

## 17. Gaps, limitacoes e pontos que devem continuar a ser apresentados com honestidade

### 17.1 O que nao deve ser sobre-afirmado

- benchmark CICIDS nao e validacao operacional real
- F1 perfeito em alguns ficheiros CICIDS nao e prova universal de generalizacao
- o retraining nao implica substituicao automatica do runtime
- LIME e counterfactuals nao explicam o score hibrido completo
- `COPY` nao e o caminho online default
- a validacao real usa logs reais com trafego controlado, nao um ambiente de producao aberto

### 17.2 Gaps ainda plausiveis

Com base no proprio material consolidado, continuam fora do ambito fechado aqui:

- cloud deployment POC
- CD automatico para producao
- promocao automatica de modelo em runtime
- cobertura final `>=85%` comprovada por evidencias presentes neste dossier
- correlacao multi-fonte mais ampla fora da familia atual de logs web

## 18. Conclusao consolidada

O pos-intermedio nao foi apenas polimento. Foi a fase em que o projeto ganhou caracteristicas que o tornam mais defensavel como sistema integrado:

- feedback humano real
- retraining assistido com promocao baseada em evidencia
- validacao ponta a ponta com logs reais
- benchmark externo complementar com protocolo honesto
- explicabilidade mais rica com LIME e counterfactuals
- workflow de incidente com auditoria
- suporte a multiplos formatos web e deduplicacao de alertas
- disciplina de schema com Alembic e fail-fast startup validation
- contrato API claro
- gate final de qualidade e reproducao

Em conjunto, estes blocos fecham uma narrativa tecnica coerente: o sistema nao e apenas um prototipo de ML, mas uma stack operacional demonstravel, auditavel e tecnicamente honesta quanto ao que faz e ao que ainda nao faz.
