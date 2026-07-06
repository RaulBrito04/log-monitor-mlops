# Week 17 - CICIDS Benchmark

## Objetivo

Adicionar uma validacao externa do metodo de deteccao sem fingir que o dataset publico usa o mesmo formato da pipeline operacional HTTP.

## Estado final

Semana fechada com benchmark real executado localmente sobre o CICIDS-2017 extraido em `data/cicids/TrafficLabelling_`.

Run usado para fechar esta semana:

```bash
bash scripts/run_cicids_benchmark.sh data/cicids/TrafficLabelling_
```

Resultado operacional do run:

- 5 ficheiros benchmarkados com sucesso
- 3 ficheiros excluidos pelo proprio protocolo temporal do benchmark
- resumo final guardado em `docs/CICIDS_BENCHMARK_RESULTS.md`
- artefactos detalhados por ficheiro guardados em `experiments/cicids/`
- logs de execucao guardados em `experiments/cicids/logs/`

## O que este benchmark faz

- carrega CSVs estilo CICIDS-2017 um a um
- normaliza colunas e labels (`BENIGN` -> 0, resto -> 1)
- faz split temporal `train / validation / test`
- aplica cap de recursos (`60000` rows no treino, `40000` em validation/test)
- treina `IsolationForest` novelty-style apenas com benign no treino
- treina `RandomForest` supervisionado no mesmo benchmark
- avalia tambem um ensemble simples `0.5 * IF + 0.5 * RF`
- escreve resultados em JSON e Markdown
- regista ficheiros excluidos com razao explicita

## Porque isto e separado da pipeline operacional

A pipeline do projeto e baseada em logs HTTP estruturados e features operacionais (`status_code`, `response_time_ms`, `endpoint_entropy`, etc.).

O CICIDS-2017 e um dataset flow-based de IDS. Por isso, este benchmark valida a **familia de metodos de deteccao** do projeto, mas **nao substitui** a validacao operacional com logs reais Nginx/Apache ja feita noutra fase.

## Leitura rapida dos resultados

- o `IsolationForest` teve comportamento muito variavel entre cenarios
- `PortScan` foi o melhor caso nao-degenerado para o `IsolationForest` (`F1 ~= 0.79`)
- `Friday Morning` foi um caso fraco para o `IsolationForest` (`F1 ~= 0.03`), coerente com o baixo volume de ataques e a dificuldade do protocolo temporal nesse ficheiro
- `WebAttacks` deu `F1 = 1.0`, mas com `ROC-AUC = nan` porque o split de teste ficou apenas com positivos; isto deve ser tratado como resultado parcial, nao como prova absoluta de generalizacao
- o ensemble foi forte onde havia sinal supervisionado suficiente, mas isso nao invalida a necessidade de manter o `IsolationForest` como mecanismo para anomalias desconhecidas

## Ficheiros excluidos e porquê

- `Monday-WorkingHours`: sem mistura suficiente de benign + ataque no treino temporal
- `Thursday-WorkingHours-Afternoon-Infilteration`: validation/test sem ataques suficientes no protocolo temporal
- `Tuesday-WorkingHours`: validation/test sem ataques suficientes no protocolo temporal

## Ficheiros principais desta semana

- `src/ml/cicids_benchmark.py`
- `scripts/run_cicids_benchmark.sh`
- `tests/unit/test_cicids_benchmark.py`
- `docs/CICIDS_BENCHMARK_RESULTS.md`
- `docs/CICIDS_BENCHMARK_METHOD.md`
