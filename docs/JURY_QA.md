# Preparação para Questões do Júri — Log Monitor MLOps
**Raúl Brito · A22309632 · IPLUSO 2026**

> Documento de preparação pessoal. Não ler em voz alta durante a apresentação.
> Organizado do mais provável para o menos provável dentro de cada categoria.

---

## ÍNDICE

1. [Machine Learning & Algoritmos](#1-machine-learning--algoritmos)
2. [Explicabilidade — SHAP, LIME e XAI](#2-explicabilidade--shap-lime-e-xai)
3. [Segurança & Security Hardening](#3-segurança--security-hardening)
4. [Arquitectura & Stack Tecnológica](#4-arquitectura--stack-tecnológica)
5. [Performance & Resultados](#5-performance--resultados)
6. [Testes, Cobertura e CI/CD](#6-testes-cobertura-e-cicd)
7. [Monitorização & Observabilidade](#7-monitorização--observabilidade)
8. [Enquadramento Regulatório](#8-enquadramento-regulatório)
9. [Benchmarking & Mercado](#9-benchmarking--mercado)
10. [Metodologia & Planeamento](#10-metodologia--planeamento)
11. [Fundamentação Científica](#11-fundamentação-científica)
12. [Viabilidade & Trabalho Futuro](#12-viabilidade--trabalho-futuro)
13. [Perguntas de Contexto Geral](#13-perguntas-de-contexto-geral)

---

## 1. Machine Learning & Algoritmos

---

**P: Porquê Isolation Forest e não outro algoritmo de anomaly detection — como Autoencoder, One-Class SVM ou LOF?**

O Isolation Forest foi selecionado por três razões práticas para este contexto específico.

Primeiro, **eficiência computacional**: o IF tem complexidade O(n log n) no treino e O(log n) na inferência. Para um sistema que precisa de processar mais de 10 mil logs por segundo, algoritmos como One-Class SVM (O(n²) a O(n³)) ou LOF (O(n²)) seriam inviáveis em tempo real.

Segundo, **robustez em dados desbalanceados e de alta dimensão**: logs de segurança têm proporções de anomalia tipicamente abaixo de 1%. O IF foi desenhado especificamente para datasets fortemente desbalanceados, ao contrário do LOF que degrada em espaços de alta dimensão.

Terceiro, **não requer dados rotulados**: o IF treina exclusivamente em dados normais. Ataques são raros, difíceis de rotular consistentemente, e mudam de natureza. Um Autoencoder precisaria de calibração constante do threshold de reconstrução. O IF isola anomalias estruturalmente, sem supervisão.

A referência base é Liu, Ting & Zhou (2008), IEEE ICDM.

---

**P: O Random Forest tem F1=0,783, inferior ao Isolation Forest com F1=0,838. Porquê usar os dois?**

A questão é legítima — em isolado, o IF supera o RF. Mas o ablation study demonstra que o ensemble supera ambos os modelos em isolado.

O motivo é complementaridade de domínio:
- O **IF** é forte em ataques nunca vistos (novelty detection) mas pode ter menor precisão em padrões já conhecidos com features subtis.
- O **RF** é supervisionado — foi treinado com labels de ataques conhecidos. É mais preciso para os padrões históricos, mas não deteta zero-days.

O ensemble captura os dois casos: o IF sinaliza o desconhecido, o RF confirma ou descarta os padrões conhecidos. O resultado combinado tem menor taxa de falsos negativos do que qualquer modelo sozinho, o que é o objetivo central num sistema de deteção de intrusões.

---

**P: O que é o protocolo "novelty-by-scenario" e como foi implementado?**

É o nome que dei ao protocolo de avaliação do Isolation Forest. A ideia é simples: o modelo é treinado **exclusivamente com logs normais** — nunca viu um ataque durante o treino. Na avaliação, é testado com ataques de categorias distintas: brute force, SQL injection, scanning, DDoS, rate abuse e path traversal.

Isto simula o cenário real de produção: um sistema de segurança precisa de detetar ataques que nunca foram catalogados antes. Se o modelo fosse treinado e testado nas mesmas categorias de ataque, estaríamos a medir memorização, não generalização.

O protocolo é inspirado na literatura de novelty detection — especificamente na distinção entre *outlier detection* (anomalias vistas no treino) e *novelty detection* (anomalias genuinamente novas). A referência formal está no paper do IF de Liu et al. (2008).

---

**P: Qual foi o dataset usado para treinar e avaliar os modelos?**

O dataset é sintético, gerado pelo próprio sistema — a Flask app tem um gerador de tráfego com seis modos: normal, brute force, scanning, SQL injection, rate abuse e mixed. Isto foi implementado na Semana 2.

A decisão de usar dados sintéticos foi deliberada: datasets reais de logs de segurança têm problemas de privacidade, desatualização rápida dos padrões de ataque, e dificuldade de reprodutibilidade. Um gerador controlado permite variar os parâmetros de ataque sistematicamente e garantir reprodutibilidade total dos experimentos.

A limitação desta abordagem é que a distribuição dos dados sintéticos pode não capturar toda a variabilidade do tráfego real. É uma limitação reconhecida — datasets como o CICIDS-2017 (Sharafaldin et al., 2018) são referência na literatura para validação mais externa, o que seria um passo natural no Goal D.

---

**P: Como foram definidos os hiperparâmetros do Isolation Forest — contaminação, número de estimadores?**

A `contamination` está definida como `'auto'` — o scikit-learn determina o threshold de decisão internamente com base na distribuição dos scores de anomalia, sem necessidade de especificar manualmente a proporção esperada de ataques.

O `n_estimators` (número de árvores do modelo) foi escolhido por grid search: o código treinou o modelo com 5 configurações distintas — 200, 300, 400 e 500 árvores, combinadas com diferentes valores de `max_samples` e `max_features` — e selecionou automaticamente a configuração com melhor F1. A configuração vencedora, que produziu F1=0,838, está registada com todos os parâmetros no MLflow e é reproduzível.

---

**P: Como é garantida a estabilidade do modelo ao longo do tempo — drift de dados?**

O MLflow regista a versão de cada modelo e as suas métricas. O Prometheus monitoriza o F1 do modelo em tempo real, com um SLO de F1 ≥ 0,75 — se o modelo degradar abaixo desse threshold, o Alertmanager dispara um alerta.

O ciclo completo de requalificação automática (retreino quando o F1 desce abaixo do SLO) está planeado para o Goal D. Neste momento, o retreino é manual mas assistido — o MLflow mostra qual o último run estável e o processo é reproduzível com um único comando.

---

**P: Porquê não usar um modelo de deep learning — LSTM, Transformer ou Autoencoder — para logs sequenciais?**

Para o contexto desta fase do projeto, os modelos clássicos foram escolhidos por três razões:

1. **Interpretabilidade**: SHAP é computacionalmente tratável em tree-based models. Em redes neurais profundas, a explicabilidade é muito mais complexa e computacionalmente cara.
2. **Volume de dados**: modelos de deep learning requerem grandes volumes de dados para superar modelos clássicos. Com dados sintéticos controlados, o IF e o RF obtêm resultados muito competitivos.
3. **Latência de inferência**: LSTMs e Transformers têm latência de inferência superior à dos modelos clássicos — incompatível com deteção em tempo real a 10k+ logs/s.

Modelos baseados em sequência (LSTM para deteção de padrões temporais) são uma extensão natural e interessante para o Goal D, mas introduziriam complexidade de explicabilidade que comprometeria a conformidade com o GDPR artigo 22 nesta fase.

---

**P: Como é feita a inferência em tempo real — o modelo está sempre em memória?**

Sim. O modelo é carregado em memória no startup do container ML Pipeline e fica residente. A inferência é feita por request de forma síncrona. O ficheiro `.pkl` é versionado no MLflow e o carregamento tem validação de integridade para prevenir deserialização de modelos não confiáveis — uma das regressões de segurança identificadas na Semana 14 e corrigida.

---

## 2. Explicabilidade — SHAP, LIME e XAI

---

**P: Como é que o SHAP satisfaz concretamente o GDPR artigo 22?**

O GDPR artigo 22 estabelece o direito a não ser sujeito a decisões exclusivamente automatizadas que produzam efeitos jurídicos significativos, e o direito a obter explicação sobre a lógica envolvida.

O SHAP (SHapley Additive exPlanations, Lundberg & Lee, 2017) calcula a contribuição marginal de cada feature para cada decisão individual, baseado na teoria dos jogos de Shapley. Para cada log classificado como anomalia, o sistema pode apresentar: quais features pesaram positivamente (aumentaram o score de anomalia), quais pesaram negativamente, e por quanto.

Isto traduz-se em linguagem auditável: "este log foi classificado como anomalia principalmente porque o número de requests por minuto é 15x superior ao baseline e o endpoint acedido é fora do padrão normal." Essa explicação é rastreável, reproduzível e não requer acesso ao modelo internamente — cobre o requisito de explicação lógica do artigo 22.

---

**P: Qual a diferença entre SHAP e LIME? Porquê usar SHAP agora e LIME no Goal D?**

SHAP e LIME são abordagens complementares, não concorrentes.

**SHAP** calcula valores globalmente consistentes com base na teoria de Shapley — garante que a soma das contribuições de todas as features é igual à diferença entre a previsão do modelo e o valor base. É matematicamente rigoroso e tem consistência global e local. A desvantagem é que pode ser computacionalmente mais exigente para modelos complexos.

**LIME** (Local Interpretable Model-agnostic Explanations) gera uma aproximação linear local à volta de um ponto de decisão. É mais rápido e funciona com qualquer modelo sem acesso à estrutura interna (model-agnostic), mas a explicação é uma aproximação local — não tem a consistência global do SHAP.

A decisão foi implementar SHAP primeiro porque é mais rigoroso, tem integração nativa com scikit-learn (TreeExplainer para modelos tree-based é extremamente eficiente), e cobre os requisitos do GDPR artigo 22 com maior robustez formal. O LIME no Goal D serve para validação cruzada das explicações — se SHAP e LIME concordam, a explicação é mais confiável; se divergem, há uma feature ou região do espaço que merece investigação.

---

**P: O ablation study — como foi estruturado e o que demonstrou?**

O ablation study compara sistematicamente três configurações do sistema de deteção:

1. **Só regras SQL**: apenas as seis regras determinísticas, sem ML.
2. **Só ML**: apenas o Isolation Forest, sem regras.
3. **Híbrido**: Rule Engine + Isolation Forest + Random Forest em ensemble.

Para cada configuração, foram medidos F1-score, precision, recall e ROC-AUC no mesmo dataset de teste. O resultado demonstrou que o híbrido supera qualquer componente em isolado em todas as métricas.

O estudo foi feito na Semana 8. A importância do ablation study é eliminar a possibilidade de que a arquitectura de ensemble seja uma escolha arbitrária — a evidência experimental justifica-a. Isto é standard em ML research e é o que dá rigor científico à escolha arquitectural.

---

**P: As explicações SHAP são estáveis entre runs? Como validas que são consistentes?**

O TreeExplainer do SHAP é determinístico para tree-based models — para o mesmo input e o mesmo modelo, a explicação é sempre idêntica. A variabilidade só surge se o modelo mudar (retreino).

A consistência entre runs é garantida pelo MLflow: cada versão do modelo tem os seus valores SHAP associados como artefactos. Se o modelo for retrained, as explicações do novo modelo podem ser comparadas com as do anterior diretamente no MLflow.

---

**P: Pode dar um exemplo concreto de uma explicação SHAP para um ataque?**

Para um ataque de brute force, a explicação SHAP poderia ser:

- `requests_per_minute`: +0.42 (a variável que mais aumentou o score de anomalia — 320 requests/min vs baseline de ~12)
- `unique_endpoints`: -0.08 (acede sempre ao mesmo endpoint — reduz ligeiramente a anomalia porque é comportamento consistente, não scanning)
- `status_4xx_ratio`: +0.31 (alta proporção de 401/403 — típico de tentativas falhadas)
- `session_duration`: +0.15 (sessão muito longa com muitos requests)

O resultado final: anomaly score = 0.87 (threshold = 0.5). A explicação mostra que a taxa de requests e a proporção de 4xx foram os fatores determinantes — é auditável e comunicável a um analista de segurança sem background em ML.

---

## 3. Segurança & Security Hardening

---

**P: Quais os OWASP Top 10 especificamente cobertos?**

O sistema cobre diretamente:

- **A01 — Broken Access Control**: os containers correm como non-root (`appuser`), sem permissões desnecessárias; os endpoints sensíveis têm autenticação.
- **A02 — Cryptographic Failures**: secrets em variáveis de ambiente, nunca em código ou ficheiros de configuração versionados.
- **A03 — Injection**: validação de inputs com Pydantic em todos os endpoints de ingestão; o Bandit deteta padrões de SQL injection no código Python.
- **A05 — Security Misconfiguration**: Trivy faz scanning das imagens Docker para identificar configurações inseguras.
- **A06 — Vulnerable and Outdated Components**: Dependabot gere automaticamente as dependências e abre PRs quando há vulnerabilidades.
- **A07 — Identification and Authentication Failures**: o Streamlit dashboard tem autenticação. Rate limiting ativo previne force brute às rotas de autenticação.
- **A09 — Security Logging and Monitoring Failures**: 9 métricas de segurança em Prometheus, dashboards de Security em Grafana, alertas no Alertmanager.

Os 54 testes de segurança automatizados validam estes controlos em cada commit.

---

**P: Como é que o rate limiting funciona — como distingue tráfego legítimo de ataque?**

O Flask-Limiter aplica limites por IP e por endpoint. As rotas de ingestão têm limites baseados no padrão de utilização normal observado durante o desenvolvimento — por exemplo, o endpoint `/ingest` aceita até X requests por segundo por IP antes de devolver 429.

Um problema identificado durante o desenvolvimento foi que o tráfego sintético de ataque gerado pelo próprio sistema (para testes) estava a ser bloqueado pelo rate limiter. A solução foi separar o tráfego de teste numa rede Docker interna com regras de rate limiting distintas, ou usar IPs de teste com whitelist no ambiente de desenvolvimento. Esta separação está documentada na secção 4.4 do relatório.

---

**P: Os dados estão encriptados em trânsito e em repouso?**

Em repouso: o PostgreSQL armazena os dados sem encriptação adicional ao nível da aplicação nesta fase — a encriptação ao nível do disco (filesystem-level encryption) depende do ambiente de deployment. Em ambiente de desenvolvimento local containerizado, este nível de encriptação está fora do âmbito.

Em trânsito: a comunicação entre microserviços é interna à rede Docker — não exposta externamente. Os endpoints externos têm HTTPS configurável mas não ativo por default nesta fase de desenvolvimento local.

A encriptação end-to-end (TLS nos endpoints externos, encriptação em repouso na base de dados) está no roadmap do Goal D para deployment externo.

---

**P: O que acontece se o utilizador `appuser` for comprometido?**

O princípio do menor privilégio limita o raio de impacto. O `appuser` não tem permissões de escrita fora do diretório da aplicação, não pode instalar software, não tem acesso a outras partes do sistema operativo, e está isolado no namespace do container Docker. Um atacante que comprometa o `appuser` não tem escalada de privilégios fácil para root dentro do container, e o isolamento Docker limita o acesso ao sistema host.

Para produção, medidas adicionais como seccomp profiles, AppArmor/SELinux e read-only filesystems estariam no plano de hardening — estão no âmbito do Goal D.

---

**P: O Bandit não deteta todos os problemas de segurança — o que fica por cobrir?**

O Bandit é uma ferramenta de análise estática Python — deteta padrões de código inseguros (uso de funções perigosas, configurações inseguras, potencial de injeção). Não cobre:

- Vulnerabilidades lógicas de negócio (ex: bypass de autenticação por lógica incorreta)
- Vulnerabilidades nas dependências de terceiros (coberto pelo Trivy e Dependabot)
- Configurações incorretas de infraestrutura
- Ataques de timing ou side-channel

Por isso o sistema usa múltiplas camadas: Bandit para análise estática de código, Trivy para scanning de imagens e dependências, Dependabot para CVEs nas dependências, e os 54 testes de segurança para validação comportamental. Defense in depth — nenhuma ferramenta é suficiente sozinha.

---

**P: Como são geridos os secrets em produção — e o ficheiro `.env` está no repositório?**

O `.env.example` está no repositório — contém apenas os nomes das variáveis, sem valores. O `.env` com valores reais está no `.gitignore` e nunca é commitado. Em produção, os secrets seriam geridos via um secret manager (HashiCorp Vault, AWS Secrets Manager, ou variáveis de ambiente do sistema de CI/CD).

O Bandit tem regras específicas para detetar hardcoded secrets no código Python. Nenhum secret está hardcoded — o Bandit confirma isso com zero findings HIGH ou MEDIUM.

---

## 4. Arquitectura & Stack Tecnológica

---

**P: Porquê Flask e não FastAPI, que é mais moderno e tem melhor performance assíncrona?**

Flask foi escolhido por três razões específicas para este projeto.

Primeiro, **ecossistema de instrumentação**: o `prometheus-flask-exporter` tem integração nativa e madura com Flask. FastAPI tem instrumentação Prometheus, mas menos testada e com mais configuração.

Segundo, **maturidade e previsibilidade**: Flask é estável, bem documentado, com comportamento previsível. FastAPI é excelente para APIs de alta concorrência assíncrona, mas adiciona complexidade com `async/await` que não era necessária para o perfil de carga deste projeto.

Terceiro, **scope do projeto**: a API de ingestão processa requests síncronos de ingestão de logs. A performance de 10.933 logs/s com Flask demonstra que não há bottleneck no framework — o bottleneck seria a base de dados antes do framework web.

Se o projeto evoluir para uma API pública com alta concorrência (Goal D), a migração para FastAPI seria uma opção válida.

---

**P: Porquê PostgreSQL + TimescaleDB e não InfluxDB ou outra base de dados de séries temporais nativa?**

O InfluxDB foi considerado mas descartado por três razões.

Primeiro, **complexidade de queries**: o Rule Engine usa SQL complexo com joins e window functions. O InfluxDB usa InfluxQL ou Flux, que têm expressividade inferior ao SQL para este tipo de queries analíticas.

Segundo, **ACID e consistência**: para logs de segurança, a garantia ACID é importante — não queremos perder eventos ou ter inconsistências. O InfluxDB prioriza disponibilidade sobre consistência.

Terceiro, **TimescaleDB é uma extensão do PostgreSQL**: o sistema herda todas as funcionalidades do PostgreSQL (SQL completo, índices B-tree, ACID) e adiciona hypertables para particionamento automático por tempo, compressão de dados históricos, e queries de séries temporais otimizadas. É o melhor dos dois mundos.

O Elasticsearch foi também considerado para o componente de pesquisa de logs mas descartado pelo overhead de infraestrutura — adicionaria um serviço separado, memória adicional, e complexidade operacional sem benefício suficiente para o âmbito atual.

---

**P: Porquê Docker Compose e não Kubernetes?**

Kubernetes é a escolha correta para clusters multi-nó com requisitos de auto-scaling, high availability, e deploy em cloud. Para este projeto, é over-engineering deliberado evitado por razões válidas.

O objetivo é demonstrabilidade local num único nó — um `docker-compose up` arranca todo o sistema em qualquer máquina com Docker. Kubernetes adicionaria: configuração de cluster, kubectl, helm charts, namespaces, ingress controllers — complexidade que não acrescenta valor ao âmbito académico e de investigação deste projeto.

Para a transição para produção multi-nó (fora do âmbito atual), a migração para Kubernetes seria natural — os Dockerfiles e as variáveis de ambiente já estão estruturados para isso.

---

**P: Como é que os 10 microserviços comunicam entre si?**

A comunicação é feita via rede Docker interna. Os serviços expõem portas internas entre si e portas externas apenas onde necessário (Flask app, Grafana, Prometheus, Streamlit, Alertmanager, MLflow).

Não há message broker (Kafka, RabbitMQ) nesta fase — a comunicação é síncrona via HTTP/REST e via base de dados partilhada (PostgreSQL). Para um sistema de produção de alta escala, um message broker entre o ingester e o pipeline ML seria a próxima evolução arquitetural — permite desacoplar a ingestão do processamento e absorver picos de tráfego.

---

**P: O que acontece se a base de dados ficar indisponível?**

Nesta fase, a base de dados é um ponto único de falha. Os containers dependem do PostgreSQL para armazenar e consultar logs. Se cair, a ingestão para.

Para produção, as mitigações seriam: PostgreSQL com réplica de read, connection pooling (PgBouncer), e um buffer de logs (fila em memória ou Redis) para absorver indisponibilidades curtas sem perda de dados. Estas medidas estão no roadmap do Goal D.

---

**P: As imagens Docker passaram de 1,2 GB para 180 MB — como foi conseguido?**

Com multi-stage builds. Um Dockerfile multi-stage divide a construção em fases:

1. **Build stage**: imagem com todas as ferramentas de compilação, dependências de desenvolvimento, e código fonte.
2. **Runtime stage**: imagem mínima (base Alpine ou slim) que copia apenas o que é necessário para executar — o binário/código compilado e as dependências de runtime.

A redução de ~85% no tamanho resulta de: eliminação das ferramentas de compilação (gcc, build-essential), eliminação de dependências de desenvolvimento (pytest, pylint, etc.), uso de imagem base mais leve (python:3.12-slim vs python:3.12).

Imagens menores significam: menor superfície de ataque, pull mais rápido, menos vulnerabilidades potenciais nas camadas da imagem.

---

## 5. Performance & Resultados

---

**P: 10.933 logs/segundo — isso é realista para produção? Qual o bottleneck?**

Para o contexto de uma PME ou aplicação web de médio porte, 10.933 logs/s representa uma capacidade de 39 milhões de logs por hora — muito superior ao volume típico de produção de uma aplicação web não-enterprise.

O bottleneck atual é a escrita no PostgreSQL. A Flask app processa requests muito rapidamente, mas cada batch de 500 logs resulta numa operação de escrita na base de dados. Com batch=1000, atingimos 13.646 logs/s (+25% sobre batch=500) — mostra que aumentar o batch size continua a ajudar, mas o ganho marginal diminui.

Para escala enterprise (centenas de milhões de logs/hora), a solução seria um message broker (Kafka) entre o ingester e o storage, com workers paralelos a escrever na base de dados. Mas para o âmbito atual — PMEs e contexto académico — 39M logs/hora é mais que suficiente.

---

**P: O teste de carga foi de 50 utilizadores durante 30 segundos. Não é demasiado curto e com poucos utilizadores?**

É um teste de referência representativo para o âmbito do projeto, não um benchmark de produção enterprise. 50 utilizadores simultâneos a enviar logs durante 30 segundos com zero falhas e mediana de 3ms é uma demonstração de que o sistema é estável sob carga — não é uma afirmação de que aguenta 50.000 utilizadores.

Para uma PME com uma aplicação web típica, 50 utilizadores simultâneos a gerar logs é um cenário realista. Para contextos de maior escala, o teste de carga seria naturalmente expandido.

O Locust permite escalar facilmente o número de utilizadores e a duração — essa expansão está no plano da Semana 15.

---

**P: O F1=0,838 — como foi calculado? Qual o dataset de teste?**

O F1 foi calculado com o protocolo novelty-by-scenario: o modelo foi treinado em logs normais e testado em logs de ataque das seis categorias (brute force, scanning, SQL injection, rate abuse, DDoS, path traversal).

F1 = 2 × (Precision × Recall) / (Precision + Recall)

Os detalhes completos estão no Apêndice F do relatório: Precision@1%=0,941, Precision@2%=0,971, ROC-AUC=0,950. Estes valores indicam que o modelo é muito preciso quando usamos o top 1% dos scores de anomalia como threshold — relevante para cenários onde os analistas de segurança processam apenas os alertas mais confiantes.

---

**P: Zero falsos positivos no Rule Engine — como é garantido?**

As regras SQL são determinísticas — definem condições exatas que devem ser satisfeitas para um log ser classificado como ataque. Por exemplo, a regra de brute force pode ser: "mais de N requests de autenticação falhada do mesmo IP num período de T segundos."

Zero falsos positivos no contexto dos testes significa que nenhum log normal gerado pelo sistema ativou uma regra de ataque. Isto é esperado porque o gerador de tráfego normal não gera padrões que satisfaçam as condições das regras.

Em produção real, com tráfego genuinamente variável, poderia haver falsos positivos — por exemplo, um utilizador legítimo a fazer muitos requests rapidamente. O ajuste dos thresholds das regras é um processo contínuo em operação. Para o âmbito atual, zero FP nos testes sintéticos valida a correção das regras.

---

**P: Pylint 9,18/10 — o que são as penalizações?**

O Pylint avalia: convenções de nomenclatura, comprimento de linhas, complexidade ciclomática, imports não usados, docstrings em falta, e padrões de código que divergem de PEP 8. Um score de 9,18 indica código de qualidade elevada com um número muito pequeno de avisos não críticos — tipicamente ausência de docstrings em algumas funções utilitárias internas ou algumas linhas ligeiramente mais longas que o limite de 100 caracteres.

O threshold de CI é 9,0/10 — qualquer degradação abaixo desse valor bloqueia o merge automaticamente.

---

## 6. Testes, Cobertura e CI/CD

---

**P: 15 testes a falhar — o que são especificamente? Isso não compromete o sistema?**

Os 15 testes que falham são falhas de configuração de fixture relacionadas com o carregamento de modelos `.pkl`. O problema foi identificado na Semana 14: o security hardening introduziu uma validação mais rigorosa no carregamento de ficheiros de modelo pickle (para prevenir deserialização de modelos não confiáveis), e os fixtures de teste não foram atualizados para o novo caminho `models/`.

Não comprometem o sistema em produção — o sistema está operacional e os modelos carregam corretamente em runtime. Comprometem apenas a suite de testes. A correção está no backlog da Semana 15 e é de baixa complexidade: atualizar os fixtures para o novo caminho.

A decisão de não bloquear o merge por estes 15 testes foi documentada como exceção conhecida. A cobertura de 70,97% é calculada sobre os testes que passam — o threshold de 70% é cumprido.

---

**P: 70% de cobertura — não é baixo? O standard da indústria não é 80% ou mais?**

O threshold de 70% foi definido como mínimo para esta fase do projeto por razões pragmáticas: código de infraestrutura (configuração Docker, scripts de setup, código de monitorização) é difícil de testar unitariamente sem mocks complexos que dariam uma cobertura artificial.

A cobertura de 70% foi validada sobre o código de lógica de negócio e pipelines — que é o que importa testar. Código de configuração de infraestrutura sem lógica condicional não contribui para a deteção de bugs e não justifica testes unitários.

O objetivo da Semana 15 é atingir 75% — com os 15 fixtures corrigidos e novos testes para o módulo de security (validators, limiter), o que é alcançável.

---

**P: O CI/CD faz deploy automático para produção?**

Não nesta fase. O pipeline atual termina em deploy local — constrói as imagens, corre os testes, e valida que o sistema arranca corretamente num ambiente containerizado local.

Deploy automático para produção (CD completo) implicaria um ambiente de produção definido (cloud provider, servidor, etc.), o que está fora do âmbito atual. O pipeline está estruturado para que a adição de um passo de deploy externo seja trivial — seria adicionar um step ao workflow GitHub Actions com as credenciais do ambiente de produção.

---

**P: Como é feito o rollback se um deploy introduzir um problema?**

Para o código: o Git tem o histórico completo. Um rollback é um `git revert` ou um checkout da versão anterior, seguido de uma nova execução do pipeline.

Para os modelos ML: o MLflow tem o histórico de versões de cada modelo. Um rollback do modelo é carregar a versão anterior a partir do MLflow registry sem necessidade de retreino.

Para a base de dados: o schema esta versionado com Alembic. O caminho normal e `alembic upgrade head` para aplicar revisoes e `alembic downgrade -1` para recuar uma revisao em ambiente controlado. O ponto delicado continua a ser gerir mudancas destrutivas ou incompativeis com dados reais, por isso cada migration deve ser pequena, reversivel e validada antes de producao.

---

**P: O Dependabot gera PRs automaticamente — como é gerido o risco de updates que quebram o sistema?**

O Dependabot abre PRs para atualizações de dependências, mas o merge é manual e sujeito ao pipeline completo de CI. Um update de dependência que quebre um teste não passa o CI e não é mergeado.

O risco real é uma dependência que muda comportamento subtilmente sem quebrar testes — o que requer testes de integração robustos para detetar. É uma limitação reconhecida e é exatamente a razão pela qual o threshold de cobertura existe.

---

## 7. Monitorização & Observabilidade

---

**P: Quais são as 9 métricas custom do Prometheus — o que medem?**

As métricas têm o prefixo `logmonitor_` e cobrem:

1. `logmonitor_logs_ingested_total` — contador de logs ingeridos
2. `logmonitor_anomalies_detected_total` — contador de anomalias detetadas pelo ML
3. `logmonitor_rule_engine_alerts_total` — contador de alertas disparados pelas regras SQL
4. `logmonitor_ingestion_latency_seconds` — latência do pipeline de ingestão
5. `logmonitor_model_f1_score` — F1 atual do modelo em produção
6. `logmonitor_request_duration_seconds` — duração dos requests HTTP
7. `logmonitor_active_connections` — conexões ativas à base de dados
8. `logmonitor_data_freshness_seconds` — tempo desde o último log processado
9. `logmonitor_security_events_total` — eventos de segurança (rate limiting, validação falhada)

A métrica `logmonitor_data_freshness_seconds` é a que disparou o alerta `DataFreshnessHigh` visível no Alertmanager — o sistema detetou que o tempo desde o último log processado excedeu o SLO de 5 minutos.

---

**P: Como são os SLOs definidos — quem os definiu e com base em quê?**

Os SLOs foram definidos com base em dois critérios: benchmarks de sistemas similares na literatura e nos requisitos não-funcionais do projeto.

- **Disponibilidade ≥ 99,5%**: standard para serviços de monitorização de segurança — abaixo disto há risco de janelas de cegueira em que ataques passam despercebidos.
- **Latência p95 < 200 ms**: baseado nos requisitos não-funcionais do sistema; a medição atual de p95=10ms está muito abaixo do threshold, o que dá margem.
- **F1 do modelo ≥ 0,75**: threshold mínimo abaixo do qual o modelo é considerado não-confiável para produção.
- **Data freshness < 5 min**: se o sistema não processar logs durante mais de 5 minutos, pode estar a perder eventos de segurança.

O Alertmanager está configurado para disparar alertas quando qualquer SLO é violado — como demonstrado pelo `DataFreshnessHigh` ativo.

---

**P: Os dashboards Grafana são provisionados automaticamente — o que acontece se a configuração YAML for inválida?**

O Grafana valida os ficheiros de provisioning no startup. Uma configuração YAML inválida resulta num erro de startup do container Grafana — detetável imediatamente no CI/CD (o step de "deploy local" verifica que todos os containers arrancam com sucesso).

Os ficheiros de provisioning estão versionados no repositório e sujeitos ao mesmo pipeline de CI — qualquer alteração que quebre o provisioning é detetada antes de chegar ao ambiente de produção.

---

## 8. Enquadramento Regulatório

---

**P: O AI Act de 2024 — este sistema é classificado como "alto risco"?**

O AI Act (Regulamento UE 2024/1689) classifica sistemas de IA em quatro categorias: inaceitável (proibido), alto risco, risco limitado, e risco mínimo.

Sistemas de IA usados em infraestruturas críticas e cibersegurança podem ser classificados como alto risco (Anexo III do AI Act). Para o contexto académico e de PME onde este sistema se posiciona, a classificação definitiva dependeria do deployment específico.

Independentemente da classificação formal, o sistema foi construído com os requisitos de alto risco em mente: transparência (SHAP), supervisão humana (os alertas requerem ação humana, não são automáticos), robustez (testes extensivos), e documentação (relatório completo e logs de MLflow). Se for classificado como alto risco em produção, o sistema já tem os controlos necessários.

---

**P: O NIS2 aplica-se diretamente a este sistema?**

A Diretiva NIS2 (2022/2555) aplica-se a operadores de serviços essenciais e fornecedores de serviços digitais — entidades específicas identificadas pelos Estados-Membros. Não se aplica diretamente ao Log Monitor MLOps enquanto sistema académico.

O que o sistema oferece é conformidade com os *princípios* do NIS2 — especificamente os artigos sobre gestão de riscos de cibersegurança, monitorização e resposta a incidentes. Uma PME que implemente o Log Monitor MLOps estaria melhor posicionada para cumprir os requisitos NIS2 se for classificada como operador de serviço essencial.

---

**P: LIME não está implementado — isso não é um problema para conformidade com GDPR artigo 22?**

O SHAP implementado é suficiente para satisfazer o GDPR artigo 22. A diretriz do artigo 22 não especifica um método de explicabilidade — exige que seja fornecida "informação significativa sobre a lógica envolvida" na decisão automatizada. O SHAP cumpre esse requisito de forma rigorosa e matematicamente fundamentada.

O LIME acrescenta uma perspetiva complementar — explicações locais aproximadas que podem ser mais intuitivas para utilizadores não técnicos. É um complemento de qualidade, não um requisito de conformidade.

---

## 9. Benchmarking & Mercado

---

**P: O Elastic SIEM é parcialmente open-source e custa entre 10 e 30 mil euros — porquê não usar isso em vez de construir do zero?**

O Elastic SIEM (Elastic Security) é open-source na sua base, mas:

1. **Sem ML adaptativo por defeito**: o ML no Elastic é uma funcionalidade paga (Platinum/Enterprise tier). A tier gratuita tem deteção baseada em regras apenas.
2. **Sem explicabilidade nativa**: não há SHAP ou LIME integrado. As decisões de ML são opacas.
3. **Complexidade de deployment**: o stack ELK (Elasticsearch, Logstash, Kibana) é pesado — tipicamente requer múltiplos servidores dedicados e expertise DevOps específico.
4. **Overhead de indexação**: o Elasticsearch indexa todos os logs para pesquisa full-text — útil para forensics mas desnecessário para um sistema focado em deteção em tempo real.

O Log Monitor MLOps faz uma coisa diferente: deteção híbrida com explicabilidade nativa e footprint mínimo. Não é um SIEM completo — é um sistema focado em anomaly detection com XAI, o que é uma categoria diferente.

---

**P: Quem são os utilizadores-alvo reais deste sistema?**

Há três perfis principais:

1. **PMEs com equipa de IT reduzida**: organizações que precisam de monitorização de segurança mas não têm budget para Splunk ou QRadar. O Log Monitor MLOps arranca com `docker-compose up` — não requer expertise especializado em SIEM.

2. **Equipas de segurança em contexto académico e de investigação**: o sistema é totalmente open-source, com código auditável, MLflow para reprodutibilidade de experimentos, e SHAP para análise de comportamento do modelo. Ideal para investigação em XAI aplicada a cibersegurança.

3. **Developers de aplicações web**: a Flask app e o pipeline de ingestão podem ser adaptados para monitorizar qualquer aplicação web — o sistema é agnóstico ao formato de log desde que os campos estejam presentes.

---

**P: Este sistema substitui um SOC (Security Operations Center)?**

Não — e essa afirmação nunca foi feita. O sistema é uma ferramenta de deteção automatizada que **suporta** analistas de segurança, não os substitui.

Os alertas gerados pelo Alertmanager e os dashboards do Streamlit e Grafana apresentam informação aos analistas humanos — a decisão de resposta (bloquear um IP, escalar um incidente, investigar um padrão) é sempre humana. O SHAP facilita essa decisão ao fornecer contexto sobre porquê um log foi sinalizado.

Esta distinção é também um requisito do AI Act: sistemas de IA de alto risco devem manter supervisão humana sobre as decisões críticas.

---

## 10. Metodologia & Planeamento

---

**P: 254 horas para um projeto desta complexidade — é realista? Como foi contabilizado?**

As 254 horas cobrem os Goals A a C (Semanas 1–14), com ~354h previstas no total incluindo o Goal D. É um projeto de semestre completo de trabalho individual.

A contabilização foi feita por semana — cada semana de implementação tem tarefas documentadas no Apêndice E do relatório. As estimativas são conservadoras: incluem desenvolvimento, testes, debugging, documentação e revisão de código, mas não leitura de literatura ou aprendizagem de novas ferramentas (que não são contabilizadas porque fazem parte da formação).

Para referência: 254 horas é equivalente a ~6 semanas e meia de trabalho a full-time (40h/semana). Para um projeto individual de licenciatura, é um volume de trabalho substancial.

---

**P: Qual foi o maior desafio técnico do projeto?**

Identifico quatro desafios técnicos documentados na secção 4.4 do relatório:

O mais significativo foi o **endurecimento do carregamento de modelos `.pkl`** na Semana 14. O security hardening introduziu validação de integridade no carregamento de ficheiros pickle (para prevenir ataques de deserialização maliciosa), o que introduziu regressões nos fixtures de teste — os testes esperavam encontrar o modelo num caminho, o código passou a usar outro. Resolver isto sem quebrar a segurança ou os testes exigiu refactoring dos fixtures.

O segundo foi a **separação entre tráfego sintético de ataque e tráfego normal** para efeitos de rate limiting. O sistema gera tráfego de ataque para testar as regras, mas o rate limiter estava a bloquear esse tráfego antes de chegar ao Rule Engine, invalidando os testes. A solução foi configurar redes Docker separadas para o tráfego de teste.

---

**P: O plano original foi cumprido? Houve desvios?**

O cronograma foi cumprido — os Goals A, B, e a primeira semana do Goal C (S14) estão entregues. Não há tarefas em atraso face ao plano original.

Os ajustes que houve foram internos a cada semana — algumas tarefas demoraram mais do que previsto e outras menos, mas os entregáveis de cada Goal foram cumpridos dentro das semanas planeadas.

O Goal D foi adicionado ao plano como extensão (Semanas 17–23) para aprofundar a API pública e o alerting avançado. Não é um desvio — foi planeado desde o início como fase de extensão.

---

**P: O que farias de diferente se recomeçasses o projeto?**

Duas coisas principais.

Primeiro, **implementar testes desde o início** em vez de adicionar cobertura retroativamente. Os 15 fixtures com problemas são consequência de ter implementado segurança depois dos testes — uma abordagem TDD teria evitado este problema.

Segundo, **começar com dados mais próximos do real** — integrar o CICIDS-2017 ou um dataset público de logs de segurança mais cedo no projeto teria dado uma validação externa mais forte aos resultados do modelo. Os dados sintéticos são suficientes para o desenvolvimento, mas a validação com dados reais é importante para credibilidade científica.

---

## 11. Fundamentação Científica

---

**P: Quais as referências científicas principais do projeto?**

O projeto referencia seis trabalhos principais:

1. **Liu, Ting & Zhou (2008)** — o paper original do Isolation Forest, IEEE ICDM. Fundamenta a escolha do algoritmo.
2. **Lundberg & Lee (2017)** — o paper original do SHAP, NeurIPS. Fundamenta a abordagem de explicabilidade.
3. **Sharafaldin, Lashkari & Ghorbani (2018)** — CICIDS-2017 dataset. Referência para datasets de deteção de intrusões.
4. **Siddiqui et al. (2019)** — KDD. Propõe especificamente a integração de anomaly detection com explicabilidade e conhecimento de domínio — o trabalho mais próximo do que este projeto implementa.
5. **Breiman (2001)** — paper original do Random Forest. Fundamenta o componente supervisionado.
6. **Zaharia et al. (2018)** — paper do MLflow. Fundamenta a plataforma de tracking de experimentos.

---

**P: O trabalho de Siddiqui et al. (2019) — como o vosso projeto se diferencia?**

Siddiqui et al. propõem uma framework teórica para integrar anomaly detection com explicabilidade e conhecimento de domínio em cibersegurança. O trabalho é principalmente conceptual e experimental em contexto de investigação.

Este projeto diferencia-se em três dimensões:

1. **Operacionalização**: o sistema é deployável — não é um experimento isolado, é uma stack completa com ingestão, armazenamento, deteção, explicabilidade, monitorização e CI/CD.
2. **MLOps**: a integração com MLflow, Prometheus, Grafana e GitHub Actions vai além do que Siddiqui et al. propõem — adiciona a dimensão de operações de ML em produção.
3. **Rule Engine híbrido**: a combinação de regras SQL determinísticas com ML não-supervisionado é um complemento não presente no trabalho de Siddiqui et al.

---

**P: Os resultados são publicáveis? Em que venue?**

Com as extensões do Goal D — especialmente a validação com o CICIDS-2017 e a implementação completa de SHAP+LIME — os resultados teriam potencial para submissão em venues como:

- **ARES Conference** (International Conference on Availability, Reliability and Security)
- **RAID** (Research in Attacks, Intrusions and Defenses)
- **IEEE S&P workshops** relacionados com ML em segurança

Nesta fase (relatório intermédio, dados sintéticos), o trabalho está no nível de um relatório técnico ou workshop paper. Com validação externa e o Goal D completo, teria substância para um paper de conferência completo.

---

## 12. Viabilidade & Trabalho Futuro

---

**P: O Goal D é realista em 7 semanas (S17–S23)?**

O Goal D prevê: API REST pública com autenticação, alerting avançado (webhooks, email), dashboard avançado para utilizadores finais, LIME, e relatório final.

É ambicioso mas plausível com ~100h adicionais (a diferença entre 254h atuais e as ~354h previstas). A infraestrutura já existe — o Goal D adiciona funcionalidades em cima de uma stack estável, não reconstrói nada do zero.

O risco principal é o scope creep — cada funcionalidade do Goal D pode expandir-se. A gestão de risco documentada no Apêndice B do relatório identifica este risco e a mitigação: priorizar a API REST e o relatório final; as funcionalidades avançadas de dashboard e alerting são "nice to have" se o tempo o permitir.

---

**P: Como é que este sistema se mantém atualizado face a novos tipos de ataque?**

Há dois mecanismos.

Para as **regras SQL**: novas regras podem ser adicionadas sem recompilar o sistema — são queries SQL numa tabela de configuração. Um analista de segurança pode adicionar uma nova regra para um padrão de ataque identificado sem tocar no código.

Para o **modelo ML**: o Isolation Forest deteta anomalias face ao baseline normal — novos tipos de ataque que produzam padrões anómalos são detetados automaticamente sem retreino. Para ataques que "parecem normais" (ex: ataques de baixa e lenta), seria necessário retreino com exemplos do novo padrão. O MLflow garante que o retreino é rastreável e reversível.

---

**P: Qual o custo de infraestrutura para correr este sistema em produção numa PME?**

Para uma PME com volume moderado de logs (até ~10M logs/dia), o sistema correria numa VPS de ~20-40€/mês (4 vCPU, 8GB RAM, SSD). O stack completo com todos os 10 containers requer cerca de 4-6GB de RAM e 2-4 vCPU em carga normal.

Comparando: Splunk Enterprise para 10GB/dia de logs custa ~50.000€/ano. O custo anual desta VPS seria ~480€. A diferença é ~49.500€/ano — para funcionalidade comparável de monitorização, com a vantagem adicional da explicabilidade nativa.

---

## 13. Perguntas de Contexto Geral

---

**P: O que é um SIEM e como este projeto se relaciona com esse conceito?**

SIEM significa Security Information and Event Management — sistemas que agregam, correlacionam e analisam eventos de segurança de múltiplas fontes em tempo real, para identificar ameaças e responder a incidentes.

O Log Monitor MLOps não é um SIEM completo — não tem connectors para múltiplas fontes de dados, gestão de casos de incidente, ou correlation rules complexas. É um sistema de **anomaly detection em logs de aplicações web** com capacidades de SIEM num âmbito mais específico: ingestão, deteção (regras + ML), explicabilidade, e monitorização.

Pode ser visto como o núcleo de ML e explicabilidade que os SIEMs comerciais deveriam ter mas não têm — e que pode ser integrado com ferramentas de SIEM mais abrangentes.

---

**P: Porquê "MLOps" no nome — o que é MLOps concretamente neste projeto?**

MLOps (Machine Learning Operations) é o conjunto de práticas que tornam os modelos de ML reproduzíveis, monitoráveis e operacionalizáveis em produção — é o equivalente de DevOps para ML.

No Log Monitor MLOps, MLOps manifesta-se em:

- **Tracking de experimentos**: MLflow regista cada run de treino com parâmetros, métricas e artefactos.
- **Versionamento de modelos**: cada modelo tem uma versão no MLflow registry.
- **Monitorização de performance**: o Prometheus monitoriza o F1 em tempo real, com alerta se degradar.
- **Feedback loop**: as deteções são armazenadas e podem ser usadas para retreino futuro.
- **CI/CD para código ML**: o pipeline de GitHub Actions inclui os testes do pipeline ML.

Sem estas práticas, um modelo de ML em produção é uma caixa-negra que pode degradar silenciosamente — o que é inaceitável num sistema de segurança.

---

**P: Este projeto é reproduzível por outra pessoa — como?**

Sim. A reprodutibilidade foi um requisito de design.

Para reproduzir o sistema completo: `git clone` do repositório, `docker-compose up`. Todos os containers arrancam, as migrations da base de dados são aplicadas automaticamente, os dashboards Grafana são provisionados, e o sistema está operacional.

Para reproduzir os experimentos de ML: os runs do MLflow estão registados com parâmetros exatos. Para reproduzir um run específico, basta executar o script de treino com os mesmos parâmetros — o resultado deve ser idêntico dado que o gerador de dados tem seed fixo.

Esta reprodutibilidade total foi validada na Semana 14 como parte do security hardening — o sistema arranca de zero num ambiente limpo sem intervenção manual.

---

**P: Qual é o contributo específico deste projeto para a área de Engenharia Informática?**

O contributo é demonstrar a **operacionalização prática** de um sistema híbrido de anomaly detection com XAI em cibersegurança, num contexto de custo zero e footprint mínimo.

A literatura tem vários trabalhos sobre anomaly detection em logs (Siddiqui et al., 2019) e sobre XAI em ML (Lundberg & Lee, 2017). O que este projeto acrescenta é a integração operacional: não apenas "o modelo funciona em laboratório" mas "o sistema está containerizado, monitorizado, com CI/CD, com alertas, com testes de segurança, e pode ser replicado com um único comando."

Essa dimensão de engenharia — a distância entre um modelo que funciona e um sistema que opera — é frequentemente subestimada na investigação académica. Este projeto demonstra que essa distância é percorrível com ferramentas open-source e sem orçamento enterprise.

---

*Fim do documento — versão 1.0 · 07/05/2026*
