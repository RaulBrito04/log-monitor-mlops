# Week 17 - CICIDS Benchmark

## Objetivo

Adicionar uma validacao externa do metodo de deteccao sem fingir que o dataset publico usa o mesmo formato da pipeline operacional HTTP.

O benchmark implementado nesta branch:

- carrega CSVs estilo CICIDS-2017 a partir de um ficheiro ou diretorio
- normaliza colunas e labels (`BENIGN` -> 0, resto -> 1)
- faz split temporal `train / validation / test`
- treina um `IsolationForest` novelty-style apenas com benign no treino
- treina um `RandomForest` supervisionado no mesmo benchmark
- avalia tambem um ensemble simples `0.5 * IF + 0.5 * RF`
- escreve resultados em JSON e Markdown

## Porque isto e separado da pipeline operacional

A pipeline do projeto e baseada em logs HTTP estruturados e features operacionais (`status_code`, `response_time_ms`, `endpoint_entropy`, etc.).

O CICIDS-2017 e um dataset flow-based de IDS. Por isso, este benchmark valida a **familia de metodos de deteccao** do projeto, mas **nao substitui** a validacao operacional com logs reais Nginx/Apache que ja foi fechada.

## Como correr

Coloca os CSVs do CICIDS-2017 numa pasta local, por exemplo:

```bash
mkdir -p data/cicids
# copiar CSVs para data/cicids/
```

Depois corre:

```bash
./venv/bin/python -m src.ml.cicids_benchmark \
  --input data/cicids \
  --report-path experiments/cicids_benchmark_report.json \
  --markdown-path experiments/cicids_benchmark_report.md
```

Tambem podes apontar diretamente para um CSV:

```bash
./venv/bin/python -m src.ml.cicids_benchmark --input data/cicids/Friday-WorkingHours-Afternoon-DDos.pcap_ISCX.csv
```

## Outputs

- `experiments/cicids_benchmark_report.json`
- `experiments/cicids_benchmark_report.md`

## O que ainda falta para fechar esta semana totalmente

- correr o benchmark com o dataset real localmente
- guardar os resultados finais do run
- comparar metricas com os resultados internos/sinteticos no relatorio
