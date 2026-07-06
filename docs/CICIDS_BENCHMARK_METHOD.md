# CICIDS Benchmark Method

## Objetivo

Este benchmark foi feito para validar externamente a familia de metodos de deteccao do projeto usando um dataset publico de IDS, sem misturar isso com a validacao operacional sobre logs HTTP reais.

## Input usado

- pasta local: `data/cicids/TrafficLabelling_`
- ficheiros: CSVs do `GeneratedLabelledFlows.zip` do CICIDS-2017
- comando principal:

```bash
bash scripts/run_cicids_benchmark.sh data/cicids/TrafficLabelling_
```

## Passo a passo high-level

1. Enumerar os CSVs no diretorio fornecido.
2. Ler um ficheiro de cada vez, com fallback de encoding (`utf-8`, `cp1252`, `latin1`).
3. Normalizar nomes de colunas e labels para um benchmark binario (`BENIGN` vs ataque).
4. Selecionar apenas features numericas utilizaveis.
5. Fazer split temporal `train / validation / test`.
6. Aplicar um cap de recursos para correr de forma reprodutivel em laptop/WSL:
   - `60000` rows no treino
   - `40000` rows em validation
   - `40000` rows em test
7. Treinar `IsolationForest` apenas com benignos do treino.
8. Treinar `RandomForest` supervisionado no mesmo split.
9. Ajustar thresholds em `validation` e avaliar em `test`.
10. Guardar um JSON e um Markdown por ficheiro em `experiments/cicids/`.
11. Agregar os runs bem-sucedidos em `docs/CICIDS_BENCHMARK_RESULTS.md`.
12. Registar ficheiros excluidos com a razao do protocolo.

## Criterios de exclusao

Um ficheiro pode ser excluido se nao sustentar o protocolo temporal definido. Nesta execucao isso aconteceu por tres razoes tipicas:

- treino sem mistura suficiente de benign e ataque
- validation/test sem ataques suficientes
- qualquer falha tecnica de parsing ou execucao

Isto nao significa automaticamente que o dataset esta mau; significa apenas que aquele ficheiro, com aquele protocolo temporal, nao permite uma comparacao justa entre modelos.

## Como interpretar os resultados

- Este benchmark e **externo** e **flow-based**.
- Nao substitui a validacao com logs reais Nginx/Apache do projeto.
- `F1` compara capacidade de deteccao no split de teste.
- `ROC-AUC = nan` significa que o split de avaliacao ficou com uma unica classe e, por isso, a ROC nao e interpretavel.
- Resultados muito altos em ficheiros muito separaveis devem ser lidos com contexto e nao como garantia de desempenho operacional.
- Resultados fracos do `IsolationForest` em certos ficheiros mostram precisamente porque e importante validar por multiplos cenarios e nao so por um dataset sintetico interno.

## Outputs gerados

- `docs/CICIDS_BENCHMARK_RESULTS.md`
- `experiments/cicids/*.json`
- `experiments/cicids/*.md`
- `experiments/cicids/logs/*.log`

## Conclusao pratica

O benchmark foi fechado de forma responsavel:

- sem alterar o dataset original
- sem fingir equivalencia com a pipeline HTTP operacional
- com exclusoes explicitas quando o protocolo nao era sustentado
- com resultados guardados num formato reutilizavel para relatorio e demonstracao
