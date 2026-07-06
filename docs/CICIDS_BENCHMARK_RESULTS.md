# CICIDS Benchmark Results

## Estado

Benchmark executado com sucesso em 5 ficheiro(s) CICIDS-2017 e excluiu 3 ficheiro(s) que nao cumpriram o protocolo ou falharam no run.

## Metodo usado

- benchmark por ficheiro CSV, nunca concatenando o dataset inteiro num unico run
- split temporal train/validation/test com cap de 60000 rows no treino e 40000 rows em validation/test para manter reproducibilidade em laptop/WSL
- isolamento do benchmark externo face a pipeline HTTP operacional do projeto
- artefactos detalhados por ficheiro guardados em `experiments/cicids/` e logs em `experiments/cicids/logs/`

## Resultados por ficheiro

| File | Rows | Attack Rows | Train | Val | Test | IF F1 | IF ROC-AUC | RF F1 | RF ROC-AUC | Ensemble F1 | Ensemble ROC-AUC |
|------|------|-------------|-------|-----|------|-------|------------|-------|------------|-------------|------------------|
| Friday-WorkingHours-Afternoon-DDos.pcap_ISCX | 225745 | 128027 | 60000 | 40000 | 40000 | 0.5318 | 0.7454 | 0.9931 | 1.0000 | 0.9911 | 0.9984 |
| Friday-WorkingHours-Afternoon-PortScan.pcap_ISCX | 286467 | 158930 | 60000 | 40000 | 40000 | 0.7942 | 0.5452 | 0.9984 | 0.9997 | 0.9963 | 0.9989 |
| Friday-WorkingHours-Morning.pcap_ISCX | 191033 | 1966 | 60000 | 38207 | 38207 | 0.0313 | 0.6240 | 0.8198 | 0.9972 | 0.0000 | 0.6695 |
| Thursday-WorkingHours-Morning-WebAttacks.pcap_ISCX | 458968 | 290782 | 60000 | 40000 | 40000 | 1.0000 | nan | 1.0000 | nan | 1.0000 | nan |
| Wednesday-workingHours.pcap_ISCX | 692703 | 252672 | 60000 | 40000 | 40000 | 0.5906 | 0.7378 | 0.9625 | 0.9957 | 0.8398 | 0.9815 |

## Destaques

- melhor F1 do Ensemble: `1.0000` em `Thursday-WorkingHours-Morning-WebAttacks.pcap_ISCX`
- melhor F1 do Isolation Forest: `1.0000` em `Thursday-WorkingHours-Morning-WebAttacks.pcap_ISCX`
- media F1 do Ensemble nos runs bem-sucedidos: `0.7655`
- media F1 do Isolation Forest nos runs bem-sucedidos: `0.5896`

## Ficheiros excluidos ou falhados

| File | Exit Code | Reason |
|------|-----------|--------|
| Monday-WorkingHours.pcap_ISCX.csv | 1 | ValueError: Training split must contain both BENIGN and attack rows for benchmark supervision |
| Thursday-WorkingHours-Afternoon-Infilteration.pcap_ISCX.csv | 1 | ValueError: Validation and test splits must contain attack rows |
| Tuesday-WorkingHours.pcap_ISCX.csv | 1 | ValueError: Validation and test splits must contain attack rows |

## Notas

- estes resultados sao de benchmark externo flow-based (CICIDS-2017)
- nao substituem a validacao operacional com logs reais Nginx/Apache ja realizada no projeto
- ficheiros benign-only ou que nao sustentem o protocolo temporal podem ser excluidos e isso deve ser explicado no relatorio
- `ROC-AUC = nan` significa que o split de teste ficou com uma unica classe e a curva ROC deixa de ser interpretavel nesse caso
- o caso `Friday-WorkingHours-Morning` mostra um cenario em que o `IsolationForest` nao generalizou bem, o que e relevante para a discussao critica no relatorio
