# Factorio Cortex Research Program

**Documento canônico do programa de pesquisa**
**Versão do programa:** Cortex Research Architecture (CRA) v0.1
**Data de instituição:** 2026-09-23/24
**Repositório:** `MrSchrodingers/project_factorio`
**Branch de transição:** `research/cortex-v1`
**Baseline pré-Cortex:** `74a1bf9c0f8792a68d7252b11d477835ec93d508`
**Tag de baseline:** `cortex-pre-research-baseline-20260923`
**Status:** Fase 0 concluída — próxima: Fase 1, Instrumentação, isolamento e baseline corrigida

> Este arquivo é o contrato científico e operacional do Factorio AI Lab. Em caso de perda de
> contexto de conversa, troca de operador, troca de modelo ou reinício do host, a continuidade
> deve partir deste documento e de `docs/CORTEX_HANDOFF.md`. Checkboxes só podem ser marcados
> quando houver evidência reproduzível, caminho de artefato e commit identificável.

---

## 1. Missão científica

O Factorio AI Lab deixa de ser tratado principalmente como uma automação para completar uma
sequência de estágios e passa a ser um laboratório para investigar **agência cognitiva,
aprendizado contínuo, memória, descoberta de estratégias e engenharia de produção autônoma** em
um ambiente espacial, parcialmente observável, acumulativo e mensurável.

A pergunta central deixa de ser:

> “O sistema consegue executar a sequência que leva ao foguete?”

e passa a ser:

> **“Um agente consegue adquirir, testar, consolidar, transferir e melhorar estratégias de
> engenharia em Factorio sem receber do programador a sequência de ações que constitui a
> solução?”**

Lançar um foguete, atingir science packs ou completar a árvore tecnológica são marcos de
capacidade, não a definição de inteligência.

### 1.1 Hipótese principal

Uma arquitetura cognitiva híbrida com:

- espaço de ações e *options* explicitamente tipados;
- memória de trabalho, episódica, semântica e procedural;
- ferramentas determinísticas de engenharia;
- política aprendida para seleção de ações;
- modelo de mundo relacional para previsão contrafactual;
- geração de hipóteses por LLM;
- avaliação externa e verificável;
- busca de diversidade e otimização morfológica;

deve apresentar **melhoria mensurável com experiência e transferência entre mundos**, em vez de
apenas reproduzir um roteiro escrito à mão.

Formalmente, queremos observar, em mundos e seeds não vistos:

[
\frac{\partial \mathbb{E}[J]}{\partial N_{\text{experience}}} > 0
]

sob orçamento de computação controlado, com (J) definido por um vetor de desempenho e não por
uma única proxy.

### 1.2 Duas vertentes de pesquisa

O programa terá duas vertentes independentes, comparáveis e eventualmente integráveis.

**Vertente A — Cortex Híbrido de Engenharia (CHE).**
Arquitetura cognitiva inspirada por CoALA, hierarchical RL, world models, quality-diversity,
program search e otimização clássica. É a linha principal para construir um agente funcional,
interpretável e experimentalmente auditável.

**Vertente B — Cortex-Fly / Connectomic Prior (CFP).**
Linha NeuroAI que testa se topologias do connectoma de *Drosophila* fornecem priors estruturais
úteis para controle e aprendizagem. O connectoma será tratado como hipótese experimental, nunca
como equivalência entre “wiring diagram” e mente.

Nenhuma vertente pode usar o sucesso da outra como evidência substituta. Integração só ocorre
após ablação independente.

---

## 2. Diagnóstico da arquitetura herdada

O handoff de 2026-09-23 em `docs/HANDOFF-CORTEX.md` mediu o problema arquitetural central:

> **O sistema tem estado, objetivo e memória. Não tem política.**

Na baseline pré-Cortex, aproximadamente 17,7 mil linhas de
`curriculum_runner.py`, `open_play_runner.py` e `evolution_loop.py` codificam, em grande
parte, a ordem das ações, posições, conexões e transições. A evolução ajusta aproximadamente 21
coeficientes escalares em torno desse roteiro. Isso é uma base de automação experimental
sofisticada, mas não constitui um agente que escolhe sua própria estratégia.

### 2.1 Estado medido antes da transição

Os valores abaixo são evidência histórica da baseline e não devem ser reinterpretados como
resultado do Cortex:

| Medida | Estado observado |
|---|---|
| geração viva na auditoria | 95 |
| champion persistido | G37 |
| `closed_loop_autonomy` do G37 | false |
| chamadas manuais de logística | 51 |
| estágio falho | Logistic science |
| halt cause | fuel_and_power_starvation |
| power-starved entities | 4 |
| fuel-starved entities | 1 |
| physical processing coverage | 16,7% |
| lições verificadas no momento da auditoria | 551 |
| lições não verificadas | 359 |

O G37 é **baseline inválida para inferência confirmatória** porque foi selecionado antes da
correção de `factory_graph.py` que invertia pickup/drop de inserters. A verificação contra a
engine encontrou 18/18 divergências. Qualquer comparação futura deve reexecutar a baseline com a
instrumentação corrigida.

### 2.2 O que é ferramenta legítima e deve permanecer

O programa não confunde “aprendizado” com “substituir algoritmos corretos por redes neurais”.
Componentes determinísticos permanecem como ferramentas do agente:

- A*/roteamento e futuros solvers de congestionamento;
- catálogo de receitas/tecnologia derivado do runtime;
- footprints e validação geométrica;
- Production DAG e material ledger;
- placement e delivery;
- resupply e rebuild transacional;
- factory graph e classificadores físicos;
- checkpoints/rollback;
- telemetria causal;
- survival analysis;
- MILP/LP/CP-SAT onde o problema for naturalmente combinatório.

A mudança é de **autoridade**: essas ferramentas deixam de ser chamadas apenas por um roteiro
central e passam a ser escolhidas por uma política cognitiva.

---

## 3. Princípios não negociáveis

### P1 — O ambiente é a autoridade

Texto do LLM, score neural, heuristic confidence ou memória não provam que algo ocorreu.
A evidência final vem do Factorio/FLE e dos instrumentos validados.

### P2 — Ausência não é zero

A família de bugs mais cara da baseline converteu “não medido” em `0` ou valor default.
Qualquer dado deve portar estado de observação:

[
x \in \{\text{observed},\text{derived},\text{estimated},\text{missing},\text{invalid}\}
]

Valores missing/invalid não podem entrar silenciosamente em fitness ou gates.

### P3 — Sem provenance, sem crédito

Toda recompensa ou capacidade deve identificar quais ações e entidades a produziram.
Produção herdada não pode ser creditada ao challenger.

### P4 — Sem baseline, sem claim

Todo módulo aprendido precisa competir contra um baseline explícito que ele de fato substituiria.

### P5 — Treinou não significa ganhou autoridade

Modelos começam em `offline`, depois `shadow`, depois `proposal`, e só então
`control-eligible`.

### P6 — Aprendizado requer escolha

Se o programa determina exatamente qual ação deve ocorrer, o sistema não aprendeu a decisão.
O runner pode existir como baseline, fixture e gerador de demonstrações, não como córtex.

### P7 — O LLM propõe; o avaliador decide

LLMs geram hipóteses, decomposições, skills e programas candidatos. Ferramentas duras filtram
viabilidade e o ambiente mede resultado.

### P8 — Ciência negativa é resultado

Se uma política neural perde para A*, uma topologia connectômica perde para GNN, ou um world
model perde para persistence, o resultado deve ser preservado. Não “salvamos” componentes por
narrativa.

### P9 — Reprodutibilidade antes de velocidade

Cada claim precisa de seed, commit, configuração, versão do ambiente, artefatos e protocolo.

### P10 — Checkboxes são evidência, não intenção

Um item `[x]` exige teste/artefato/commit. Trabalho iniciado permanece `[ ]` com nota de status.

---

## 4. Arquitetura Cortex de referência

### 4.1 Estado cognitivo

O estado do agente não será um prompt monolítico. Será um objeto tipado:

[
B_t = (W_t, G_t, A_t, M_t, U_t, C_t)
]

onde:

- (W_t): belief sobre o mundo físico;
- (G_t): goal stack e prioridades;
- (A_t): affordances/actions atualmente possíveis;
- (M_t): memórias recuperadas;
- (U_t): incertezas relevantes;
- (C_t): constraints de tecnologia, material, segurança e compute.

O belief state deve separar **fato observado**, **inferência** e **previsão**.

### 4.2 Espaço mínimo de ações primitivas

A Fase 2 deve expor, com schemas tipados e contratos verificáveis:

- `OBSERVE(region, channels)`
- `PLACE(prototype, anchor, constraints)`
- `CONNECT(source, target, commodity, constraints)`
- `FEED(target, commodity, quantity_or_rate)`
- `CRAFT(item, amount)`
- `RESEARCH(technology_or_capability)`
- `DEMOLISH(selection, reason)`
- `WAIT(condition_or_horizon)`
- `CHECKPOINT(label)`
- `ROLLBACK(checkpoint, reason)`

Cada ação devolve um `ActionResult` com outcome observado, custo, duração, efeitos, recusas,
proveniência e eventuais violações.

### 4.3 Options / habilidades temporais

A unidade cognitiva principal não deve permanecer na granularidade FLE. A arquitetura adotará
*options* no sentido de Sutton, Precup & Singh:

[
o = (I_o,\pi_o,\beta_o)
]

com initiation set, política interna e condição de término.

Exemplos:

- `ESTABLISH_MINING_CELL(resource, target_rate)`
- `ESTABLISH_SMELTING_ZONE(item, target_rate)`
- `EXPAND_POWER(required_capacity, reserve)`
- `CREATE_PRODUCTION_BLOCK(product, target_rate)`
- `CONNECT_FLOW(source, sink, target_rate)`
- `REBALANCE_NETWORK(commodity)`
- `REBUILD_REGION(region, objective)`
- `INVESTIGATE_ANOMALY(signal)`
- `RESEARCH_TOWARD(capability)`
- `EXPAND_INDUSTRIAL_ZONE(zone, forecast)`

Uma option pode chamar A*, MILP, placement, resupply ou outras ferramentas sem forçar o
executive a raciocinar novamente sobre cada tile.

### 4.4 Loop cognitivo

O caminho principal da Vertente A será:

```text
OBSERVE
  ↓
BELIEF UPDATE
  ↓
GOAL / BOTTLENECK SELECTION
  ↓
MEMORY RETRIEVAL
  ↓
CANDIDATE HYPOTHESES / OPTIONS
  ↓
HARD FEASIBILITY FILTER
  ↓
COUNTERFACTUAL EVALUATION
  ↓
POLICY / VALUE SELECTION
  ↓
TRANSACTIONAL EXECUTION
  ↓
VERIFY PREDICTION
  ↓
CREDIT ASSIGNMENT
  ↓
EPISODIC WRITE
  ↓
CONSOLIDATION / LEARNING
```

O `repair_loop.py` existente é o protótipo funcional mais próximo desse circuito e deve ser
generalizado, não descartado.

---

## 5. Memória cognitiva

### 5.1 Working memory

Curta duração, limitada por orçamento. Contém:

- objetivos ativos;
- último conjunto de observações;
- gargalos;
- candidatas atuais;
- hipóteses causais;
- incertezas;
- commitments ainda não resolvidos.

### 5.2 Episodic memory

A unidade canônica será uma transição causal:

[
e_t=(s_t,g_t,a_t,\hat{\Delta}s_{t+1},u_t,s_{t+1},r_t,c_t)
]

incluindo previsão antes da ação e observação depois dela. O armazenamento deve permitir
consultas por similaridade, estrutura e contexto operacional.

### 5.3 Semantic memory

Lições consolidadas não podem ser meras frases. Uma regra deve carregar:

- proposição;
- escopo;
- evidência;
- support count;
- counterexamples;
- confidence/calibration;
- data da última revalidação;
- versões do ambiente em que vale;
- causal strength quando aplicável.

### 5.4 Procedural memory

Skills/options comprovadas formam uma biblioteca reutilizável com:

- preconditions;
- effects previstos;
- custo esperado;
- distribuição de sucesso;
- falhas conhecidas;
- dependências;
- proveniência;
- exemplos/contraexemplos;
- versão.

Isso se inspira no valor composicional observado por Voyager, mas skills só entram na biblioteca
após validação externa.

### 5.5 Consolidação

Entre gerações, um processo offline deverá executar:

[
\text{replay} \rightarrow \text{clustering} \rightarrow \text{credit assignment}
\rightarrow \text{rule induction} \rightarrow \text{validation}
\rightarrow \text{skill update} \rightarrow \text{forgetting}
]

O objetivo é distinguir armazenamento de aprendizado: acumular JSONL não basta.

---

## 6. Decisão tipada e incerteza

O Cortex não deve receber como unidade primária strings como “acho melhor aumentar cobre”.
A interface de decisão deve produzir objetos consumíveis por software, por exemplo:

```text
Decision
  intent
  target
  candidate_option
  parameters

Prediction
  expected_effects
  risk_distribution
  bottleneck_after

Uncertainty
  feasibility_probability
  outcome_probability
  epistemic_flags

Evidence
  episodes
  semantic_rules
  model_rollouts
```

O trabalho da TypeSafe AI/Jev é uma referência conceitual recente para “typed probabilistic
decisions” e calibração. O projeto **não assume** as alegações comerciais da empresa como fato
científico; a ideia transferida é: decisões estruturadas + probabilidade explícita são
operacionalmente superiores a texto livre quando software precisa agir.

---

## 7. Engenharia de fábrica e morfologia industrial

### 7.1 Representação hierárquica

[
World \supset Zones \supset ProductionCells \supset Machines
]

e, em paralelo:

[
ResourceGraph \rightarrow LogisticsGraph \rightarrow ProductionGraph
\rightarrow TechnologyGraph
]

O Cortex precisa raciocinar sobre **zonas industriais e redes**, não só máquinas isoladas.

### 7.2 Vetor objetivo

Não será adotada uma soma opaca de métricas heterogêneas. O vetor de desempenho inclui, no mínimo:

[
\mathbf{J}=(-T,A,L,C,W,E,R,D,X,Q)
]

onde:

- (T): throughput sustentado;
- (A): área/footprint;
- (L): distância logística ponderada pelo fluxo;
- (C): congestionamento e conflitos;
- (W): WIP;
- (E): energia e custo operacional;
- (R): custo esperado de rebuild/reconfiguração;
- (D): intervention debt;
- (X): capacidade de expansão futura;
- (Q): robustez/survival sob perturbações.

Seleção pode ser Pareto/lexicográfica conforme o experimento.

### 7.3 Roteamento

A* permanece baseline legítimo para rota individual. Para redes densas, a pesquisa deve testar
roteamento com congestão negociada (família PathFinder), comparando comprimento total,
conflitos, throughput e custo computacional.

### 7.4 Rebuild e ALNS

A fábrica será um objeto explícito passível de *ruin-and-recreate*. Operadores de destruição devem
ser variados e adaptativos:

- região geométrica;
- seção de produção;
- gargalo;
- rotas congestionadas;
- ativos subutilizados;
- seleção aleatória estratificada.

A aceitação deve considerar benefício futuro **e custo de interrupção/reconstrução**.

---

## 8. World model relacional

A GRU vetorial existente permanece baseline histórica, não arquitetura final presumida.

Factorio é relacional. O modelo de mundo candidato será orientado a grafo:

[
s_{t+1}=F_{explicit}(s_t,a_t)+\Delta_{\theta}(G_t,a_t)
]

onde o modelo explícito cobre receitas, capacidades e constraints conhecidas, enquanto
(Delta_\theta) aprende resíduos como starvation, congestionamento, atraso, interações e efeitos
não capturados.

Um world model só ganha autoridade quando melhorar **decisão downstream**, não apenas MSE.

Protocolo mínimo:

1. persistence baseline;
2. modelo explícito;
3. ESN/GRU histórico;
4. Graph World Model;
5. ablação sem ações;
6. ablação sem estrutura de grafo;
7. avaliação de policy regret usando cada modelo.

DreamerV3 é referência porque usa o world model para imaginar consequências de ações, não como
mero painel preditivo.

---

## 9. Policy learning e Harnessed Agentic RL

A execução do Factorio e o treinamento devem permanecer desacoplados.

[
Runtime \rightarrow TrajectoryStore \rightarrow OfflineLearner
\rightarrow Challenger \rightarrow Shadow \rightarrow Authority
]

Agent Lightning é uma referência importante para essa separação entre harness e trainer.

A política seguirá uma progressão de complexidade:

1. regras explícitas como baseline;
2. contextual bandit apenas onde existir gap de arms;
3. supervised value/ranking sobre episódios;
4. offline RL;
5. online constrained RL somente em arena transacional;
6. model-based planning quando world model tiver evidência downstream.

Nunca aplicar RL apenas porque “é mais IA”.

---

## 10. LLM, program synthesis e descoberta

O Qwen local deve migrar de mutador de hiperparâmetros para três papéis:

1. **gerador de hipóteses/opções candidatas**;
2. **explicador de falhas e gerador de counterfactuals**;
3. **sintetizador de skills/programas candidatos**.

Inspirado por FunSearch e AlphaEvolve:

[
LLM \rightarrow CandidateProgram \rightarrow Evaluator
\rightarrow Archive \rightarrow Selection \rightarrow LLM
]

O evaluator nunca pode ser substituído pelo julgamento do próprio LLM.

Áreas candidatas para program search:

- prioridade de zoning;
- heurísticas de rebuild;
- ordem de expansão;
- alocação de buffers;
- heurísticas de congestionamento;
- seletores de subproblemas;
- funções de score de layout;
- operadores ALNS.

---

## 11. Quality-Diversity

Champion único não é suficiente para um domínio com muitos estilos de fábrica.

MAP-Elites será usado para preservar famílias de soluções sob descritores como:

- footprint;
- throughput;
- comprimento de rede;
- WIP;
- energia;
- intervenção;
- modularidade;
- expansão futura;
- estratégia morfológica.

Métricas: coverage, QD-score, elite turnover, novelty e desempenho por célula.

Descritores mal escolhidos são hipótese falsificável: se elites diferentes não correspondem a
diferenças úteis, o arquivo deve ser redesenhado ou removido.

---

# VERTENTE B — CORTEX-FLY / CONNECTOMIC PRIOR

## 12. Motivação

FlyWire reconstruiu o cérebro adulto feminino de *Drosophila melanogaster* com 139.255 neurônios
e 54,5 milhões de sinapses. Em 2026, o Male CNS connectome expandiu o recurso para 166.691
neurônios cobrindo cérebro e ventral nerve cord; o Google Research reporta aproximadamente
125 milhões de conexões sinápticas.

Esses mapas fornecem topologia estrutural extraordinária, mas **não são uma mente simulada**.

A literatura “A connectome is not enough” explicita elementos ausentes: dinâmica celular,
receptores, neuromoduladores, gap junctions, hormônios, plasticidade e acoplamento corpo-cérebro.

Logo, esta vertente testa a hipótese mais defensável:

> **A topologia connectômica pode atuar como prior estrutural útil para aprendizagem e controle.**

## 13. FlyGM como referência metodológica

FlyGM (2026) transforma o connectoma estático em um directed message-passing graph e relata
melhor sample efficiency/desempenho em controle locomotor que controles rewired, random e MLP.

A transferência para Factorio deve repetir o espírito experimental, não a narrativa.

### H-FLY-1

[
H_0: J(G_{fly}) \le J(G_{control})
]

[
H_1: J(G_{fly}) > J(G_{control})
]

sob orçamento de parâmetros, dados, seeds e compute pareados.

### 13.1 Controles obrigatórios

- connectome real;
- degree-preserving rewired connectome;
- random directed graph pareado;
- GNN convencional;
- Graph Transformer;
- MLP parametrically matched.

Sem esses controles, qualquer melhora pode ser apenas efeito de largura, sparsity ou depth.

## 14. Mapeamento sensório-motor experimental

O primeiro experimento não tentará “simular todos os neurônios biologicamente”.

Entradas do Cortex-Fly devem vir de features compactas e tipadas:

- produção;
- starvation;
- energia;
- ocupação espacial;
- recurso disponível;
- tecnologia;
- congestão;
- novelty;
- goal embedding.

Saídas não controlarão tiles diretamente. Elas selecionarão **options** do mesmo espaço da
Vertente A. Assim, as duas arquiteturas compartilham ambiente e evaluator.

## 15. Plasticidade e memória neuroinspirada

Mushroom body, dopaminergic reinforcement e central complex fornecem inspirações funcionais para
experimentos de:

- sparse associative memory;
- reward-modulated plasticity;
- heading/goal integration;
- action gating;
- multiple-timescale memory.

Essas inspirações precisam ser implementadas como hipóteses matemáticas explícitas. Nomes
neuroanatômicos sem correspondência funcional mensurável são proibidos.

## 16. NeuroMechFly como guardrail epistemológico

NeuroMechFly v2 demonstra o valor de combinar connectome-constrained networks, corpo, sensores e
RL. Para o Factorio, a analogia correta é “estrutura + embodiment + feedback”, não “copiar o
cérebro e esperar inteligência”.

---

# PROTOCOLO CIENTÍFICO

## 17. Hierarquia de evidência

1. unit/integration test;
2. deterministic replay;
3. paired seed experiment;
4. held-out seed;
5. multi-seed evaluation;
6. ablation;
7. robustness perturbation;
8. independent rerun after fresh process/reboot where applicable.

Claims de “aprendizado” requerem níveis 5 e 6 no mínimo.

## 18. Métrica primária

A métrica operacional primária é **produção autônoma sustentável em holdout sem intervenção**.

Durante a janela:

- nenhuma chamada manual de logística permitida;
- production throughput medido da engine;
- starvation observado;
- capacidades simultâneas verificadas;
- provenance exigida.

## 19. Métricas cognitivas

- prediction calibration;
- expected calibration error;
- Brier score para decisões probabilísticas;
- prediction-held rate;
- policy regret;
- sample efficiency;
- transfer gain para seeds novos;
- recovery time após perturbação;
- memory utility por ablação;
- skill reuse rate;
- catastrophic forgetting;
- proposal feasibility rate;
- evaluator rejection rate;
- causal attribution accuracy.

## 20. Métricas de engenharia

- throughput/item/s;
- flow-weighted logistics distance;
- belts/pipes/poles por unidade de throughput;
- area/throughput;
- WIP;
- buffer occupancy;
- energy/unit;
- downtime;
- congestion;
- rebuild cost;
- expansion headroom;
- intervention debt.

## 21. Estatística

Para resultados estocásticos:

- seeds pareadas sempre que possível;
- mínimo inicial de 5 seeds para exploração e 10 para claims confirmatórios, sujeito a power analysis;
- IQM e intervalos bootstrap para comparação agregada;
- distribuição completa e não apenas best run;
- effect size;
- teste de permutação onde apropriado;
- correção para múltiplas comparações quando houver famílias de hipóteses;
- protocol version congelado antes da avaliação confirmatória.

A literatura de Henderson et al., Agarwal et al. e Colas et al. fundamenta a exigência de múltiplas
seeds e incerteza explícita.

## 22. Goodhart guard

Nenhum score agregado pode ocultar regressões críticas.

Critérios duros:

- intervenção não pode ser mascarada por throughput;
- starvation crítico não pode ser mascarado por área;
- produção herdada não pode ser creditada;
- missing não vira zero;
- “stage completed” não substitui holdout físico.

---

# PROGRAMA FASEADO

## Regra de preenchimento

Para marcar `[x]`, adicionar na mesma fase:

- **Evidence:** caminho(s) de arquivo;
- **Tests:** comandos e resultado;
- **Commit:** SHA;
- **Decision:** pass/fail e motivo.

Uma fase só fecha quando seu Exit Gate estiver atendido.

---

## Fase 0 — Constituição científica e baseline reprodutível

**Objetivo:** impedir que a refatoração comece sem contrato experimental, lineage e fronteira clara
entre automação herdada e arquitetura-alvo.

- [x] instituir este documento como contrato canônico;
- [x] criar `docs/CORTEX_HANDOFF.md`;
- [x] atualizar README para a missão científica;
- [x] criar branch `research/cortex-v1`;
- [x] marcar baseline `74a1bf9` com tag imutável;
- [x] registrar árvore suja pré-existente sem incorporá-la silenciosamente;
- [x] expor no frontend “Programa Cortex”, fase e duas vertentes;
- [x] validar documentação e frontend;
- [x] commit separado da infraestrutura científica;
- [x] push da branch e tag;
- [x] registrar checkpoint no SentinelX.

**Exit Gate F0:** alguém sem contexto de chat consegue identificar estado, princípios, fases,
baseline, próximos passos e evidência necessária apenas pelo repositório.

**Evidence:** `docs/CORTEX_RESEARCH_PROGRAM.md`, `docs/CORTEX_HANDOFF.md`,
`docs/baselines/CORTEX_PHASE0_BASELINE.md`, `README.md` e bloco Cortex servido pelo dashboard.
**Tests:** 50 testes de dashboard aprovados; `compileall` do dashboard; `node --check app.js`;
probes HTTP confirmaram as duas vertentes no frontend vivo.
**Commit:** `767b9238202b021ff1c5679eeca4d19f72b39f12` — `feat: institui programa científico Cortex`.
**Publication:** `origin/research/cortex-v1` e tag `cortex-pre-research-baseline-20260923` publicadas.
**Continuity:** SentinelX `sxc_4557STHZ`, revisão 5.
**Decision:** **PASS — F0 concluída.** A próxima fase autorizada é F1; F2 permanece bloqueada até
a baseline corrigida e o isolamento operacional estarem concluídos.

---

## Fase 1 — Instrumentação, isolamento e baseline corrigida

**Objetivo:** produzir uma baseline cientificamente válida antes de comparar o Cortex.

- [x] finalizar/validar mudanças logísticas já não commitadas;
- [x] snapshot de runs e artefatos históricos;
- [x] desativar G37 como baseline confirmatória;
- [x] reexecutar baseline com factory graph corrigido;
- [x] separar código “deployed” de edição live;
- [x] impedir geração com working tree suja de ser promoted;
- [x] declarar build/revision no relatório de toda geração;
- [x] criar esquema de Missing/Observed/Derived/Estimated;
- [x] auditoria sistemática de defaults que transformam missing em número;
- [x] hardening do serviço LLM: investigar RSS, MemoryMax/limite, restart policy e soak;
- [x] garantir restart do container Factorio;
- [x] protocolo de backup/reset versionado;
- [x] congelar conjunto de seeds de baseline;
- [x] separar runtime do dashboard do runtime científico sob teste;
- [x] declarar evidence scope do dashboard por arena/seed;
- [x] impedir mistura de estado global legado com seed baseline isolada;
- [x] rotular mapa como mundo RCON ao vivo e evidência como sandbox da seed;
- [x] criar phase state legível por máquina para retomada;
- [x] criar launcher detached com pin explícito de release/commit;
- [x] impedir avanço para nova seed quando uma seed estiver running;
- [x] bloquear launch com armazenamento crítico;
- [x] limitar logs Docker do cluster Factorio;
- [x] produzir relatório estatístico da baseline.

**Status F1:** **PASS — instrumentação, isolamento e baseline exploratória corrigida concluídos.**

**F1-B result:** 5/5 exploratory seeds valid. All five are partial_success and fail at
Logistic science with zero green-science output; closed-loop autonomy is 0/5. Median autonomy
score = 0.50; median physical processing coverage = 0.50; median manual logistics = 55 calls.
Isolation PASS in all five. The structural placement repair is repeatedly diagnosed but cannot
execute because the inherited runner has no_runner_binding_for_intent.

The confirmatory seeds 20261101–20261110 remain frozen and unspent. They are reserved for future
paired pre-Cortex vs Cortex evaluation and must not be used for tuning.

**Observability hardening:** dashboard evidence scope, map truth labels and component-isolated
deployment are specified in docs/CORTEX_DASHBOARD_EVIDENCE_SCOPE.md.

**Continuity hardening:** detached seed launch, machine-readable phase reconstruction and
anti-duplication resume protocol are specified in docs/CORTEX_CONTINUITY_PROTOCOL.md.

**Storage hardening:** launch headroom gate and Factorio Docker log rotation are specified in
docs/CORTEX_F1_STORAGE_HARDENING.md.

**Evidence F1-A:** commits a3a50b5, f8437d5 e e5cd102; snapshot
backups/cortex-f1-pre-20260924T031901Z; docs/audits/NUMERIC_DEFAULT_AUDIT_F1.md; artefatos de
soak em runs/audits; release imutável sob /srv/factorio-ai-runtime/current.

**Tests F1-A:** 1283 testes core/FLE + 2 testes PyTorch; 56 testes focados de semântica/provenance;
Ruff, compileall, bash -n e diff-check aprovados.

**Exit Gate F1:** baseline reproduzível, instrumentação válida e nenhuma promoção dependente de
código não identificado.

**Evidence F1:** docs/CORTEX_PHASE1_INTEGRITY.md; docs/CORTEX_PHASE1_BASELINE_PROTOCOL.md;
docs/CORTEX_PHASE1_BASELINE_RESULTS.md; docs/CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md;
docs/CORTEX_PHASE1_BASELINE_SEED_20261001.md até CORTEX_PHASE1_BASELINE_SEED_20261005.md;
runs/audits/cortex_baseline_exploratory_summary.json; isolation snapshots por seed.

**Tests F1 closure:** 74 testes focados dashboard/baseline/continuity PASS; analyzer enriquecido
6/6 PASS; full gate 1308 core/FLE PASS + 2 PyTorch PASS; Ruff/static/frontend TypeScript/Vite
build PASS; provenance e isolation PASS em 5/5 seeds.

**Commit F1 evidence:** `9cffaca5401bcfb97d5c8472ec619a34abfa563f` —
`feat: conclui baseline exploratória da fase um`.

**Decision F1:** **PASS.** A baseline pré-Cortex está causalmente caracterizada e reproduzível.
F2 está autorizada. Claims confirmatórios de superioridade permanecem bloqueados até avaliação
pareada nas seeds confirmatórias congeladas.

---

## Fase 2 — Action/Option Ontology e Universal Executor

**Objetivo:** dar ao agente um espaço de decisão real.

- [x] schemas para ações primitivas;
- [x] `ActionRequest`, `ActionResult`, `Refusal`, `EvidenceRef`;
- [x] facade sobre placement/delivery/resupply/rebuild/research/craft;
- [x] contratos pré/pós-condição;
- [x] provenance por ação;
- [x] refusals nomeadas;
- [ ] transactional execution universal;
- [ ] options iniciais;
- [ ] teste que constrói cadeia funcional sem `curriculum_runner`;
- [ ] runner antigo executável apenas como baseline.

**F2-A progress:** PASS parcial. Ontology tipada e UniversalExecutor em SHADOW foram
implementados sem alterar o runtime científico F1. Evidence: docs/CORTEX_PHASE2_ACTION_ONTOLOGY.md,
src/factorio_ai_lab/cortex/actions.py, src/factorio_ai_lab/cortex/executor.py e
tests/test_cortex_actions.py.

**Authority F2-A:** SHADOW apenas. Nenhum novo handler live foi ligado ao Cortex.

**Next:** F2-B — adapters transacionais com parity tests, começando por resupply e power-tap,
seguindo para place_processing_for_buffered_output, o counterexample estrutural replicado em F1.


**Evidence F2-A:** docs/CORTEX_PHASE2_ACTION_ONTOLOGY.md; src/factorio_ai_lab/cortex/actions.py;
src/factorio_ai_lab/cortex/executor.py; tests/test_cortex_actions.py; phase-state e dashboard atualizados.

**Tests F2-A:** 68 focused PASS; full gate 1318 core/FLE PASS + 2 PyTorch PASS; Ruff, compileall,
node --check e frontend TypeScript/Vite build PASS.

**Commit F2-A:** `6c50e3bc90505bb27d74431a8f01ac06d42ed9e5` —
`feat: institui ontologia de ações do Cortex`.

**Decision F2-A:** **PASS parcial de F2.** A ontology, provenance, refusals, pre/postconditions e
facade universal existem em SHADOW. Transactional universal execution permanece bloqueada até F2-B.

**F2-B progress:** **PASS parcial.** Parity adapters para resupply e power-tap produzem o mesmo
script, purpose e refusal semantics dos handlers legados, sem world mutation. Evidence:
`docs/CORTEX_PHASE2_LEGACY_PARITY.md`, `src/factorio_ai_lab/cortex/legacy_parity.py` e
`tests/test_cortex_legacy_parity.py`.

**Tests F2-B:** 69 focused PASS; full gate 1323 core/FLE PASS + 2 PyTorch PASS; static checks PASS.

**Commit F2-B:** `7c38cd7c9f0f67068e449fa3e5ff107b74ebcbd1` —
`feat: adiciona adapters de paridade do Cortex`.

**Decision F2-B:** **PASS parcial de F2.** Transactional execution continua aberta. O gap
`place_processing_for_buffered_output` permanece deliberadamente unbound e passa a ser F2-C.

**F2-C progress:** **PASS parcial.** O intent estrutural place_processing_for_buffered_output agora possui planner puro/shadow genérico. Ele infere material por nearest-buffer evidence, escolhe transformação direta não ambígua via runtime catalog, resolve processor via DependencyPlanner e procura conjuntamente placement + delivery. Não há world mutation.

**Live shadow evidence F2-C:** mundo com 125 entidades, 6 producers, coverage físico 0.5. Targets automáticos: u1778/u1838/u1839. Para u1839 foi produzido branch iron-ore -> iron-plate, stone-furnace, placement (35,85), delivery por um inserter e hard structural preconditions satisfeitas. u1778 recusado por buffer vazio; u1838 recusado por buffer contaminado com coal + iron-ore. Artifact: runs/audits/cortex_f2c_live_structural_shadow.json.

**Evidence F2-C:** docs/CORTEX_PHASE2_STRUCTURAL_PLANNING.md; src/factorio_ai_lab/cortex/structural.py; tests/test_cortex_structural.py; phase-state/dashboard atualizados.

**Tests F2-C:** 116 focused PASS; full gate 1333 core/FLE PASS + 2 PyTorch PASS; Ruff/static checks e frontend TypeScript/Vite build PASS.

**Commit F2-C:** `cacf3b7eae8e8a3a8c6b9e5471ec942ba7548e0f` — `feat: adiciona planejamento estrutural do Cortex`.

**Decision F2-C:** **PASS parcial de F2.** O gap replicado da F1 deixou de ser unbound quando há evidence material suficiente. Transactional execution universal continua aberta; buffer contaminado permanece refusal até ResourceSurvey/mining-target evidence.

**F2-D progress:** **PASS.** ResourceSurvey sobre o footprint do
producer agora tem precedência causal sobre downstream buffer contents. Buffer contaminado não
destrói identidade quando o recurso minerado é observado; mixed-resource footprint permanece
refusal e não há majority heuristic. ProcessingBranch compila para PreparedStructuralAction
versionado e inerte, sem TransactionalFLEExecutor.

**Live shadow evidence F2-D:** targets automáticos u1778/u1838/u1839. u1778 foi identificado como
coal por mining_resource mesmo com buffer vazio; u1838 e u1839 foram identificados como iron-ore.
Os dois iron producers foram agrupados em um branch iron-ore -> iron-plate / stone-furnace,
placement aproximadamente (30,85), delivery por inserter e PreparedStructuralAction ready=true.
Coal permaneceu refusal estrutural por ausência de transformação direta. Artifact:
runs/audits/cortex_f2d_live_structural_shadow.json.

**Evidence F2-D:** docs/CORTEX_PHASE2_STRUCTURAL_PREPARATION.md;
src/factorio_ai_lab/cortex/structural.py;
src/factorio_ai_lab/cortex/structural_prepare.py;
tests/test_cortex_structural.py; phase-state/dashboard context atualizados.

**Tests F2-D:** 44 focused PASS; 1340 core/FLE PASS + 2 PyTorch PASS; Ruff/static checks e frontend TypeScript/Vite build PASS.

**Commit F2-D:** `19e0c4fb57f7aa8dbd34c272324383617f7916d1` — `feat: adiciona identidade causal e preparação estrutural`.

**Decision F2-D:** **PASS parcial de F2.** Resource identity causal e PreparedStructuralAction estão validados em SHADOW. F2-E está autorizada somente para controlled transactional execution com authority explícita, rollback e hard postconditions medidas.

**F2-E1 progress:** **IMPLEMENTADO / canário real pendente.** StructuralTransactionalAdapter
compila PreparedStructuralAction para FLE validado, exige EXECUTE explícito e usa
TransactionalFLEExecutor como única fronteira de commit/rollback. Hard guards exigem aumento de
producers_reaching_processor, processor_exists == true e processor_output crescente. Engine,
measurement e hypothesis failures permanecem causalmente distintos.

**Evidence F2-E1:** docs/CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md;
src/factorio_ai_lab/cortex/structural_execute.py;
scripts/run_cortex_structural_canary.py;
tests/test_cortex_structural_execute.py;
tests/test_cortex_structural_canary_contract.py.

**Tests F2-E1:** 35 continuity/execution focused PASS; full gate 1363 core/FLE PASS + 2 PyTorch PASS; Ruff/static e frontend TypeScript/Vite PASS; dry-run fail-closed com world_mutation=false.

**Reboot hardening:** fd9da1ae3999b549aa4026186cce5b0caaca0b6d preserva game_knowledge_graph válido quando o probe vivo retorna agent-character unavailable. Após regeneração oficial: 217 recipes, 196 technologies, 14 machines; os 10 failures transitórios passaram 10/10.

**Commit F2-E1:** 7552140bb6575ec9faad94436633e77eb4949ed5 — feat: adiciona execução transacional controlada do Cortex.

**Decision F2-E1:** **PASS parcial de F2.** F2-E2 está autorizado somente como one-shot controlled Factorio canary em seed 424242, partindo de árvore clean e evolution inactive/disabled.

**F2-E2 attempt 1:** instrumentation counterexample preservado em
runs/audits/cortex_f2e_structural_canary_attempt1.json. O experimento terminou antes de EXECUTE
porque _save_entity_state não fornecia o schema científico esperado pelo planner. Não houve
ActionResult nem structural transaction.

**Instrumentation fix:** commit 4a0558e3a4c6b7795d618f4cdcda3a22e53074d8 alinha o canário ao
FactorioObserver.snapshot/resource_overview/game_knowledge, restringe available ao inventory do
character e mede processor_output pelo craft_output observado.

**F2-E2 attempt 2:** executado em seed 424242 a partir do commit limpo 4a0558e3a4c6b7795d618f4cdcda3a22e53074d8.

**Candidate measurements:**

- producers_reaching_processor: 0 -> 1;
- physical_processing_coverage: 0.0 -> 1.0;
- processor_exists: false -> true;
- processor_status: no_fuel;
- processor_output: 0.0 -> 0.0.

**ActionResult:** rejected / structural_postcondition_failed. As hard conditions topológicas
passaram, mas processor_output INCREASE falhou. transaction_committed=false e
rollback_observed=true. A medição final retornou ao estado before.

**Evidence attempt 2:** runs/audits/cortex_f2e_structural_canary.json e cópia imutável
runs/audits/cortex_f2e_structural_canary_attempt2.json, SHA-256
2783803e228cf59b2048aeac9a4c5c28a27c0b8ac51eb5a89cea5d97d2d4770e.

**Decision F2-E:** **PASS para controlled transactional execution; FAIL para suficiência funcional
da opção estrutural atual.** O sistema demonstrou EXECUTE explícito, medição pós-ação e rollback
real no Factorio. O counterexample mostra que placement+material delivery não bastam: o processor
precisa de fuel/energy dependency explícita. O output gate não será relaxado.

**Closure gate F2-E2:** 1368 core/FLE PASS + 2 PyTorch PASS; continuity/outcome PASS;
Ruff/static + frontend TypeScript/Vite + node PASS; runtime F1 unchanged; evolution
inactive+disabled; rollback exactness PASS.

**F2-E2 closure snapshot commit:** b61f199febd83a5c4aa9a1187f5f15eaecfe25e6 — feat: fecha canário transacional F2-E2.


**F2-F1 progress:** **PASS.** O runtime catalog agora mede
energy_source_type, max energy usage por tick e fuel categories de máquinas, além de FuelSpec
runtime com fuel value/category. O observer mantém compatibilidade com Factorio 2.0.73: tenta
fuel_categories e, quando o accessor não existe, mede o legado fuel_category sem converter absence
em default.

**Live evidence F2-F1:** stone-furnace = burner, 1500 J/tick, category chemical. O runtime expôs
6 fuels totais e cinco fuels chemical compatíveis: nuclear-fuel, rocket-fuel, solid-fuel, coal e
wood. Essa ordenação é factual/determinística por densidade energética; não é ainda uma policy de
seleção.

**Evidence F2-F1:** docs/CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY.md;
src/factorio_ai_lab/dashboard/state.py;
src/factorio_ai_lab/planning/runtime_catalog.py;
tests/test_runtime_machine_data.py.

**Tests F2-F1:** 53 focused PASS; 1373 core/FLE PASS + 2 PyTorch PASS; Ruff/static,
py_compile, frontend TypeScript/Vite e diff check PASS; live read-only game_knowledge probe PASS.

**Decision F2-F1:** **PASS.** O runtime agora fornece o substrato energético necessário para
F2-F2 sem hardcodes de machine/fuel. Nenhuma nova authority de escrita foi criada.

**F2-F1 commit:** 32ee7e0dfc74bf22d4aabb8b442b7683cb3f98ba — feat: instrumenta dependências energéticas do Cortex.

**F2-F2 progress:** **IMPLEMENTADO / full gate pendente.** Burner sizing foi generalizado para
fuel values medidos; MachineEnergy gera demanda; RuntimeFactorioCatalog filtra fuels compatíveis;
plan_supply decide cobertura usando carried inventory e FuelSource observado. Apenas candidato
realmente coberto é selecionado.

**Typed dependency:** FuelDependency preserva machine, MachineEnergy, horizon, FuelSpec, units_needed
e SupplyPlan. O PreparedStructuralAction é promovido para cortex_structural_ops_v2 e recebe
preflight.energy_dependency + operação semântica fuel_processor antes de connect_delivery.

**Execution boundary:** carried-only fuel compila para insert_item no cortex_processor. SupplyPlan
com world fuel draw continua sendo recusado por structural_world_fuel_draw_unsupported até existir
adapter específico que preserve provenance.

**Evidence F2-F2:** docs/CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY_COMPOSITION.md;
src/factorio_ai_lab/cortex/functional_dependency.py;
src/factorio_ai_lab/cortex/structural_prepare.py;
src/factorio_ai_lab/cortex/structural_execute.py;
src/factorio_ai_lab/planning/fuel.py;
tests/test_cortex_functional_dependency.py;
tests/test_fuel.py.

**Tests F2-F2:** 83 focused PASS; 1386 core/FLE PASS + 2 PyTorch PASS; Ruff/compileall/frontend TypeScript/Vite/node/diff check PASS; runtime F1 unchanged; evolution inactive+disabled.

**F2-F2 commit:** `95ec3dfc23c3d39a88fc6b5abe64e9042902a413` — `feat: compõe dependência funcional de combustível`.

**Decision F2-F2:** **PASS.** A composição causal de fuel está validada e publicada. F2-F3 está
autorizada somente como one-shot isolated canary em seed 424242, com runner versionado e árvore
clean. Continuous authority permanece proibida.

**F2-F3 result:** **VALID COUNTEREXAMPLE / rollback PASS.** O canário real em
`e1aad03dafa8604a002ca11641f0000b69cbefa0`, seed 424242, alcançou a capability F2-F2.
FunctionalDependency ficou ready=true, selecionou coal por compatibilidade + disponibilidade,
inseriu `fuel_processor` no contrato e removeu o erro `no_fuel`.

Candidate state:

- producers_reaching_processor: 0 -> 1;
- physical_processing_coverage: 0.0 -> 1.0;
- processor_exists: false -> true;
- processor_status: `no_ingredients`;
- processor_output: 0.0.

A hard postcondition funcional `processor_output INCREASE` permaneceu unsatisfied, portanto a
transação foi rejeitada e rollback_observed=true. O estado final retornou ao baseline do canário.

**Scientific interpretation:** F2-F2 resolveu fuel do processor, mas a hipótese de que fuel era a
única dependência funcional ausente foi falsificada. O delivery contract usa
`entities=["inserter"]`; `structural_prepare.py` seleciona esse actuator explicitamente e
`planning/delivery.py` não modela power. No canário não há power network. F2-F4 deve portanto
modelar energia/capability do delivery actuator genericamente.

**Artifact F2-F3 válido:** `runs/audits/cortex_f2f_structural_canary.json`.

Uma execução posterior falhou no fixture producer+buffer antes da capability. Ela foi preservada
como `cortex_f2f3_structural_canary_attempt1_invalid_bootstrap*.json` e classificada como invalid
experiment / fixture timing failure. O bootstrap foi endurecido com polling bounded 1 s,
deadline 12 s e telemetria de polls/elapsed/iron_buffered; isso não altera a capability Cortex.
Hardening commit: `0a747b99328bf084a82fdde79ee8259018ba7931`.

**Next:** F2-F4 — delivery actuator dependency / energy-aware delivery. Manter
`processor_output INCREASE` inalterado, compor dependencies tipadas e validar primeiro em
shadow/replay. F3 permanece bloqueada.

**Exit Gate F2:** o agente pode montar uma cadeia funcional escolhendo primitivas/options por uma
API genérica, sem caminho codificado por estágio.

---

## Fase 3 — Executive / Cognitive Loop

**Objetivo:** generalizar repair loop para decisão dirigida por objetivos.

- [ ] BeliefState tipado;
- [ ] GoalStack;
- [ ] deficit/goal diagnosis unificado;
- [ ] candidate generator;
- [ ] hard feasibility filter;
- [ ] choice policy interface;
- [ ] prediction before action;
- [ ] verification after action;
- [ ] credit assignment;
- [ ] experiment ledger;
- [ ] shadow comparison contra runner.

**Exit Gate F3:** mesmo objetivo gera alternativas e escolhas observáveis; sequência não está
embutida em um stage handler.

---

## Fase 4 — Memória cognitiva e consolidação

- [ ] working memory;
- [ ] episodic store;
- [ ] semantic store versionado;
- [ ] procedural skill library;
- [ ] retrieval híbrido estrutural + similaridade;
- [ ] counterexamples como primeira classe;
- [ ] confidence/support/validity scope;
- [ ] consolidation job;
- [ ] forgetting/decay policy;
- [ ] memória ablation experiment;
- [ ] transferência entre seeds.

**Exit Gate F4:** remover memória causa perda estatisticamente detectável em tarefas de
transferência; memória deixa de ser apenas logging.

---

## Fase 5 — Política aprendida e Agentic RL desacoplado

- [ ] trajectory schema;
- [ ] offline dataset versioning;
- [ ] rule baseline;
- [ ] contextual bandit apenas onde houver arms distinguíveis;
- [ ] value/ranking model;
- [ ] offline RL challenger;
- [ ] shadow mode;
- [ ] proposal mode;
- [ ] authority gate;
- [ ] regret/calibration metrics;
- [ ] harness/train separation inspirado em Agent Lightning.

**Exit Gate F5:** escolha aprendida supera baseline de decisão em holdout, com CI e ablação.

---

## Fase 6 — Zoning, morfologia industrial e network design

- [ ] Zone / ProductionCell abstractions;
- [ ] factory solution object;
- [ ] flow-weighted layout cost;
- [ ] congestion-aware routing;
- [ ] A* sequential baseline;
- [ ] ALNS/LNS destroy operators;
- [ ] adaptive operator weights;
- [ ] rebuild acceptance com downtime;
- [ ] expansion headroom metric;
- [ ] Pareto/QD descriptors;
- [ ] benchmark em mapas novos.

**Exit Gate F6:** sistema descobre e melhora morfologias sem receber main-bus/city-block como regra.

---

## Fase 7 — Graph World Model e imaginação contrafactual

- [ ] graph state encoder;
- [ ] explicit dynamics interface;
- [ ] residual GNN;
- [ ] action-conditioned prediction;
- [ ] uncertainty;
- [ ] multi-step rollout;
- [ ] persistence baseline;
- [ ] GRU/ESN baselines;
- [ ] downstream policy evaluation;
- [ ] model-based candidate scoring;
- [ ] authority gate.

**Exit Gate F7:** world model melhora decisões futuras, não apenas loss de previsão.

---

## Fase 8 — Skill/program discovery: FunSearch/AlphaEvolve track

- [ ] sandbox de programas;
- [ ] evaluator determinístico;
- [ ] candidate database;
- [ ] island/diversity mechanism;
- [ ] LLM program proposer;
- [ ] program mutation;
- [ ] lint/type/security gates;
- [ ] replay before live;
- [ ] discovery benchmark;
- [ ] skill promotion protocol.

**Exit Gate F8:** ao menos uma heurística/skill nova supera baseline humana/determinística em
holdout e é reproduzível.

---

## Fase 9 — Cortex-Fly / FlyGM track

- [ ] adquirir dataset connectômico com licença/proveniência;
- [ ] definir subset/whole graph experimental;
- [ ] input adapters;
- [ ] output/readout adapters para options;
- [ ] directed message passing implementation;
- [ ] degree-preserving rewired control;
- [ ] random graph control;
- [ ] GNN control;
- [ ] Graph Transformer control;
- [ ] MLP matched control;
- [ ] parameter/compute matching;
- [ ] multi-seed sample-efficiency study;
- [ ] topology ablation;
- [ ] plasticity variants;
- [ ] relatório negativo/positivo sem cherry-picking.

**Exit Gate F9:** claim sobre connectome suportado ou refutado por controles pareados.

---

## Fase 10 — Integração CHE + CFP e currículo emergente

- [ ] common action space;
- [ ] common evaluator;
- [ ] common memory schema;
- [ ] automatic goal frontier;
- [ ] dependency-derived curriculum;
- [ ] runner stages removidos do caminho principal;
- [ ] cross-architecture arena;
- [ ] transfer benchmark;
- [ ] open-play zero-intervention qualification;
- [ ] multi-seed survival.

**Exit Gate F10:** agente progride em open-play sem stage script e mantém cadeia produtiva autônoma.

---

## Fase 11 — Perturbações, adversários e robustez

- [ ] resource perturbation;
- [ ] machine failure simulation quando possível;
- [ ] power shocks;
- [ ] blocked routes;
- [ ] demand shifts;
- [ ] biters somente após maturidade econômica;
- [ ] recovery policy;
- [ ] robust planning;
- [ ] continual-learning forgetting tests.

**Exit Gate F11:** adaptação a mudanças sem reset completo e sem intervenção humana.

---

## Fase 12 — Reprodutibilidade científica e publicação

- [ ] experiment registry;
- [ ] immutable manifests;
- [ ] dataset cards;
- [ ] model cards;
- [ ] ablation matrix completa;
- [ ] statistical notebooks/scripts;
- [ ] figures reproduzíveis;
- [ ] limitations;
- [ ] negative results;
- [ ] replication instructions;
- [ ] release archive;
- [ ] preprint/paper draft.

**Exit Gate F12:** terceiro operador consegue reproduzir os principais claims a partir de release
congelada.

---

# 23. Perguntas de pesquisa formais

**RQ1.** Memória episódica/semântica/procedural melhora transferência entre seeds?
**RQ2.** Prediction-before-action melhora credit assignment e aprendizado?
**RQ3.** Uma política aprendida supera regras no mesmo espaço de options?
**RQ4.** Um Graph World Model residual melhora decisão downstream?
**RQ5.** Quality-Diversity encontra morfologias melhores/diversas que champion único?
**RQ6.** Congestion-aware network routing supera A* sequencial em redes densas?
**RQ7.** ALNS aprende quando reconstruir e o que destruir?
**RQ8.** LLM + evaluator descobre heurísticas novas com ganho verificável?
**RQ9.** Decisões tipadas/calibradas reduzem ação inválida em relação a texto livre?
**RQ10.** Priors connectômicos melhoram sample efficiency vs rewired/random/GNN?
**RQ11.** Skills consolidadas reduzem custo deliberativo e catastrophic forgetting?
**RQ12.** O agente aprende conceitos funcionais equivalentes a zonas/backbones sem recebê-los como
receitas fixas?

---

# 24. Riscos operacionais conhecidos

- checkout ativo historicamente serviu também como deploy;
- working tree pode mudar enquanto uma geração roda;
- G37 tem provenance de instrumentação inválida;
- `factorio-ai-llm` já apresentou crescimento severo de RSS; na auditoria atual estava em ~8 GB;
- host é CPU-only, 8C/16T, ~15 GiB RAM;
- FLE 0.4.3 possui comportamentos/bugs documentados no handoff;
- container Factorio historicamente não tinha restart automático;
- `/tmp` pequeno já produziu falsos negativos de testes;
- bytecode Python por mtime/size já contaminou validação;
- calls FLE podem reportar “sucesso” sem efeito físico completo.

Nenhuma fase de treino pesado começa antes dos hardenings F1.

---

# 25. Política de reset

Reset é permitido, mas nunca “apagamento para fazer funcionar”.

Antes de qualquer reset:

1. snapshot/backup do estado;
2. manifest com commit + dirty state + hashes;
3. motivo científico;
4. definição do que é preservado;
5. teste de restauração;
6. novo seed/lineage identificado.

A baseline inválida pode ser aposentada, não destruída.

---

# 26. Frontend como instrumento científico

O dashboard deve evoluir de monitor operacional para cockpit experimental. Deve expor:

- fase Cortex atual;
- Vertente A / Vertente B;
- hipótese ativa;
- baseline vs challenger;
- autoridade de cada módulo;
- memória recuperada para a decisão;
- candidata escolhida e alternativas;
- previsão antes da ação;
- observação depois da ação;
- calibration/error;
- experiment lineage;
- ablation flags;
- QD archive;
- world-model eligibility;
- FlyGM/control topology quando a Fase 9 iniciar.

Visualização nunca pode conferir autoridade inexistente a módulo “treinado mas não usado”.

---

# 27. Referências primárias e técnicas

### Ambiente e agentes

- Hopkins, J.; Bakler, M.; Khan, A. **Factorio Learning Environment** (2025).
  https://arxiv.org/abs/2503.09617
- Sumers, T. R. et al. **Cognitive Architectures for Language Agents (CoALA)** (2023).
  https://arxiv.org/abs/2309.02427
- Wang, G. et al. **Voyager: An Open-Ended Embodied Agent with Large Language Models** (2023).
  https://arxiv.org/abs/2305.16291
- Microsoft Research. **Agent Lightning v1.0: Towards Harnessed Agentic RL** (2026).
  https://www.microsoft.com/en-us/research/publication/agent-lightning-v1-0-towards-harnessed-agentic-rl/

### World models e RL hierárquico

- Hafner, D. et al. **Mastering diverse control tasks through world models**. Nature 640,
  647–653 (2025). https://doi.org/10.1038/s41586-025-08744-2
- Sutton, R. S.; Precup, D.; Singh, S. **Between MDPs and semi-MDPs: A framework for temporal
  abstraction in reinforcement learning**. Artificial Intelligence 112 (1999).
  https://doi.org/10.1016/S0004-3702(99)00052-1

### Program search e descoberta

- Romera-Paredes, B. et al. **Mathematical discoveries from program search with large language
  models**. Nature 625, 468–475 (2024). https://doi.org/10.1038/s41586-023-06924-6
- Google DeepMind. **AlphaEvolve: A Gemini-powered coding agent for designing advanced
  algorithms** (2025). https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

### Quality-diversity e avaliação

- Mouret, J.-B.; Clune, J. **Illuminating search spaces by mapping elites** (2015).
  https://arxiv.org/abs/1504.04909
- Henderson, P. et al. **Deep Reinforcement Learning that Matters** (2018).
  https://arxiv.org/abs/1709.06560
- Agarwal, R. et al. **Deep Reinforcement Learning at the Edge of the Statistical Precipice**
  (2021). https://arxiv.org/abs/2108.13264
- Colas, C.; Sigaud, O.; Oudeyer, P.-Y. **How Many Random Seeds?** (2018).
  https://arxiv.org/abs/1806.08295
- Karwowski, J. et al. **Goodhart's Law in Reinforcement Learning**.
  https://arxiv.org/abs/2310.09144

### Connectomics / NeuroAI

- Dorkenwald, S. et al. **Neuronal wiring diagram of an adult brain**. Nature (2024).
  https://doi.org/10.1038/s41586-024-07558-y
- Schlegel, P. et al. **Whole-brain annotation and multi-connectome cell typing of Drosophila**.
  Nature (2024). https://doi.org/10.1038/s41586-024-07686-5
- Google Research. **A connectomics milestone: Mapping the complete male fruit fly brain**
  (2026). https://www.research.google/blog/a-connectomics-milestone-mapping-the-complete-male-fruit-fly-brain/
- Jin, Z. et al. **Whole-Brain Connectomic Graph Model Enables Whole-Body Locomotion Control in
  Fruit Fly (FlyGM)** (2026). https://arxiv.org/abs/2602.17997
- Wang-Chen, S. et al. **NeuroMechFly v2**. Nature Methods 21, 2353–2362 (2024).
  https://doi.org/10.1038/s41592-024-02497-y
- Scheffer, L. K.; Meinertzhagen, I. A. **A connectome is not enough**. J Exp Biol 224 (2021).
  https://doi.org/10.1242/jeb.242740

### Decisão tipada

- TypeSafe AI. **Introducing System One Models & Jev** (2026).
  https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Ropke, S.; Pisinger, D. **An Adaptive Large Neighborhood Search Heuristic for the Pickup and Delivery Problem with Time Windows**. Transportation Science 40(4), 455–472 (2006).
  https://doi.org/10.1287/trsc.1050.0135

As alegações de fornecedores/comercial devem ser reproduzidas localmente antes de serem tratadas
como evidência.

---

# 28. Documentos relacionados

- `docs/HANDOFF-CORTEX.md` — diagnóstico histórico que motivou a mudança;
- `docs/pesquisa-metodologia.md` — revisão metodológica anterior;
- `docs/ML_ARCHITECTURE.md` — arquitetura ML herdada;
- `docs/EVOLUTION.md` — seleção evolutiva herdada;
- `docs/CORTEX_HANDOFF.md` — checkpoint operacional atual;
- `README.md` — entrada para operadores e pesquisadores.

---

# 29. Critério de sucesso do programa

O programa não termina quando um foguete é lançado.

Ele terá demonstrado sucesso científico se pudermos mostrar, com ablações e incerteza, que um ou
mais mecanismos — memória, policy learning, world model, program search, quality-diversity ou
connectomic prior — causam ganho reproduzível em **aprendizado, transferência, autonomia,
eficiência ou adaptação** em mundos não vistos.

E terá igual valor científico se algumas dessas hipóteses forem refutadas com rigor.
