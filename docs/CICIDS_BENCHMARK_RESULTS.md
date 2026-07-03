# CICIDS Benchmark Results

## Estado

Pendente execucao com o dataset CICIDS-2017 real nesta maquina.

## Runner validado

O runner `scripts/run_cicids_benchmark.sh` foi smoke-tested com um fixture temporario apenas para validar o fluxo tecnico de execucao.

Esse smoke test **nao** conta como benchmark externo real e por isso nenhum valor de F1/ROC-AUC de fixture deve ser usado no relatorio.

## Como gerar este ficheiro com dados reais

Quando os CSVs estiverem disponiveis localmente:

```bash
bash scripts/run_cicids_benchmark.sh data/cicids
```

ou

```bash
CICIDS_INPUT=data/cicids/Friday-WorkingHours-Afternoon-DDos.pcap_ISCX.csv bash scripts/run_cicids_benchmark.sh
```

O runner vai:

- executar `src/ml/cicids_benchmark.py`
- gerar `experiments/cicids_benchmark_report.json`
- gerar `experiments/cicids_benchmark_report.md`
- copiar o resumo Markdown para este ficheiro

## O que deve ser revisto depois do run

- F1 e ROC-AUC do `IsolationForest`
- F1 e ROC-AUC do `RandomForest`
- F1 e ROC-AUC do `Ensemble`
- diferenca face aos resultados internos/sinteticos
- limitacoes metodologicas por ser um benchmark flow-based e nao a pipeline HTTP operacional
