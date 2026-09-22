# Pesquisa de metodologia: do Factorio Learning Environment a um agente que joga

Data: 2026-09-22

Este documento e um levantamento de literatura primaria sobre os metodos aplicaveis ao problema
de um agente que constroi, reconstroi e otimiza fabricas em Factorio, com critica metodologica de
cada familia e recomendacoes priorizadas. Nao e um relatorio de auditoria do codigo deste
repositorio, nao contem medicoes feitas pelo autor sobre o sistema atual, e nao substitui a
execucao dos experimentos que propoe.

## Convencao de verificacao

Cada referencia recebe uma marca conforme o nivel de checagem efetivamente realizado:

- **sem marca** — texto integral, ou a secao relevante dele, foi aberto e lido; as citacoes
  literais deste documento vem dessa leitura.
- **`[abstract]`** — a fonte primaria foi aberta e o resumo lido; o texto integral nao foi lido.
- **`[nao verificado]`** — existe apenas registro bibliografico (API Crossref ou meta-tags da
  pagina do editor), ou a fonte estava inacessivel. Autor, ano, venue e paginas estao conferidos;
  o conteudo **nao** esta. Afirmacoes sobre esses trabalhos neste documento se limitam ao escopo
  declarado (o que o trabalho e e o que propoe), nunca a numeros internos.

Contagem: **42 referencias distintas** deste documento estao marcadas `[nao verificado]`. Cinco
delas aparecem duas vezes no texto (uma vez na referencia completa, uma vez em tabela de resumo);
a contagem e de referencias distintas, nao de ocorrencias da marca.

## Duas ressalvas que condicionam a leitura

1. **Contagem de tarefas do FLE diverge entre versoes.** O arXiv v1 diz "eight structured tasks";
   o PDF hospedado no site do projeto diz "24 structured tasks"; a pagina do NeurIPS 2025 diz
   "33 bounded tasks". O PDF final no OpenReview esta atras de verificacao de browser e **nao foi
   aberto**. Onde o numero de tarefas importar, essa divergencia precisa ser resolvida na fonte.
2. **Os sete fatos medidos do projeto foram declarados, nao reproduzidos.** Campeao congelado na
   geracao 6 com 13 geracoes rejeitadas; r=0,031 (n=30) entre parametro mutado e resultado; teste
   de permutacao p=0,62 no bandit de posicionamento; world model perdendo para persistencia em 4/4
   folds e nunca carregado em inferencia; spatial policy com 7 empates e 1 pior em 8 rotas contra
   A*; cobertura fisica mineracao->processamento de 16,7%; metricas de producao constantes
   (138 placas / 92 cobre / 10 ciencia) com chamadas manuais quadruplicadas. O autor deste
   documento **nao inspecionou o codigo nem reexecutou essas medicoes**. A secao 5 esta
   condicionada a correcao delas.

---

## 1. Fonte primaria do ambiente (FLE)

**Hopkins, J.; Bakler, M.; Khan, A. "Factorio Learning Environment". arXiv:2503.09617v1,
submetido em 6 de marco de 2025 (cs.MA; cs.CL; cs.LG).**
https://arxiv.org/abs/2503.09617 — https://arxiv.org/html/2503.09617v1

Aceito no **NeurIPS 2025** (poster): https://neurips.cc/virtual/2025/poster/121827
Codigo: https://github.com/JackHopkins/factorio-learning-environment — "An open source framework
for developing and evaluating LLM agents in the game of Factorio", v0.3.0.
Versao intermediaria em PDF (a das 24 tarefas):
https://jackhopkins.github.io/factorio-learning-environment/assets/documents/paper.pdf

### 1.1 O que o ambiente mede

| Metrica | Definicao | Propriedade relevante |
|---|---|---|
| Production Score (PS) | Valor economico de todos os itens produzidos, usando o sistema de precos interno do Factorio; itens com cadeia mais longa valem mais | "naturally unbounded measure of performance" — nao satura |
| Milestones | Contagem de tipos de item novos produzidos e tecnologias pesquisadas | Mede amplitude na arvore tecnologica |
| Lab-play (sucesso) | Binario: a fabrica sustenta o throughput-alvo (**16/s solidos, 250/s fluidos**) durante uma **janela de holdout de 60 segundos** | E o unico instrumento do benchmark que separa automacao de producao manual |
| Task progress | Percentual dos ingredientes-alvo e sub-ingredientes produzidos por passo | Curva, nao escalar final |

A API expoe 23 metodos em tres grupos: consultas puras (`get_entities`, `nearest`,
`production_stats`, `inspect_inventory`), modificacoes de estado (`place_entity`, `rotate_entity`,
`craft_item`, `set_recipe`, `connect_entities`) e gestao de recursos (`insert_item`,
`harvest_resource`, `extract_item`). O paper registra que `connect_entities` e a operacao mais
lenta da API, 25 a 48 ops/s, "due to pathfinding requirements", contra 276 a 545 ops/s das
operacoes de inventario. O servidor headless atinge media de 218 ops/s; o interpretador Python
introduz cerca de 3x de sobrecarga, reduzindo a media a 68 ops/s.

### 1.2 Baselines reportados

**Nenhum baseline humano e nenhum baseline programatico.** Citacao literal do paper:

> "it is unclear if achieving end-game goals (e.g. escape the world or build rockets) is
> achievable to humans using only an API in a reasonable time-frame. We did however prove that
> each step in the chain to launch a rocket was achievable from the previous step and that all
> tasks in lab-play can be completed."

E, em seguida:

> "Even without human baselines, we believe that FLE is a useful benchmark, as the comparative
> scores between agents still informs us of their relative ability at planning, spatial reasoning
> and resource management."

Consequencia direta para este projeto: o ambiente de referencia **nao fornece** baseline. Se o FLE
nao tem, o agente local precisa construir os seus.

### 1.3 Resultados reportados (Tabela 1, arXiv v1)

| Modelo | Sucesso em lab-play (%) |
|---|---|
| Claude 3.5 Sonnet | 21,9 +/- 1,3 |
| GPT-4o | 16,6 +/- 1,4 |
| Deepseek-v3 | 15,1 +/- 1,7 |
| Gemini-2 Flash | 13,0 +/- 1,3 |
| Llama-3.3-70B | 6,3 +/- 1,0 |
| GPT-4o-Mini | 5,2 +/- 0,6 |

Open-play: Claude com 293.206 PS e 28 milestones; Llama-3.3-70B com 54.998 PS e 26 milestones.

### 1.4 O que os autores dizem sobre LLMs neste dominio

1. **Raciocinio espacial deficiente.** Falhas recorrentes: "trying to place entities too close or
   on-top of each other, not leaving room for connections or incorrect placement of inserters".
2. **Incapacidade de melhorar iterativamente.** Os agentes acabam "breaking existing structures
   during the process" ao tentar escalar.
3. **Loops de depuracao degenerados.** Em lab-play, **56% dos passos em execucoes bem-sucedidas
   geraram erro de execucao**; em open-play, entre 29,7% e 76,4%. Caso registrado: "GPT-4o used the
   same API method incorrectly for **78 contiguous steps**, receiving identical error message each
   time".
4. **Diagnostico no nivel errado.** "agents often focused on whether all singular entities were
   working but did not investigate whether the **topology** of the whole structure was correct".
5. **Objetivos miopes.** "Gemini-2.0 manually crafted 300+ wooden chests over 100 steps".

### 1.5 Calibracao para este projeto

O item 5 e decisivo. O PS conta itens produzidos **inclusive por crafting manual**. Um agente pode
elevar PS sem automatizar coisa alguma. O padrao descrito na auditoria interna — producao vinda de
`insert_item`/`extract_item` com cobertura fisica de 16,7% — e o mesmo modo de falha que o
benchmark de origem documenta em modelos de fronteira. O antidoto ja existe dentro do proprio FLE:
o criterio de lab-play, throughput sustentado por 60 segundos de holdout, que logistica manual nao
consegue falsificar.

Existe ainda um antecedente especifico de Factorio como problema de otimizacao, citado pelo FLE:

**Reid, K. N.; Miralavy, I.; Kelly, S.; Banzhaf, W.; Gondro, C. "The Factory Must Grow: Automation
in Factorio". arXiv:2102.04871, 9 de fevereiro de 2021 (submetido a GECCO 2021).**
https://arxiv.org/abs/2102.04871

Define o *logistic transport belt problem*, apresenta um **modelo de programacao inteira** dele
(variaveis binarias de colocacao por posicao, tipo e direcao; restricoes duras de viabilidade;
restricoes suaves de razao correta de saida, chegada ao destino, desvio de obstaculos e
minimizacao do numero de esteiras), fornece interface para otimizadores externos e compara
**Simulated Annealing, Genetic Programming e Evolutionary Reinforcement Learning**. E a referencia
mais proxima do problema concreto de esteiras.

---

## 2. A analogia do xadrez, corrigida

### 2.1 Por que Deep Blue funcionou

**Campbell, M.; Hoane Jr., A. J.; Hsu, F.-h. "Deep Blue". *Artificial Intelligence* 134(1-2):57-83,
2002.** DOI `10.1016/S0004-3702(01)00129-1`.
Texto lido em copia espelhada: https://www.mimuw.edu.pl/~ewama/zsi/deepBlue.pdf
(O original no ScienceDirect retorna HTTP 403: https://www.sciencedirect.com/science/article/pii/S0004370201001291 — **nao aberto**.)

Do texto: motor de busca de xadrez em chip unico; sistema massivamente paralelo com multiplos
niveis; enfase forte em extensoes de busca; funcao de avaliacao complexa; uso de base de partidas
de Grandmaster. Numeros conferidos no artigo: **480 chess chips** (16 por processador SP, 30 nos);
**cerca de 200 milhoes de posicoes por segundo**; avaliacao em hardware que passou de
aproximadamente 6400 para **mais de 8000 features**; "A three minute search on Deep Blue would
reach a full-width depth of **12.2** on average". As features foram "created/tuned by hand", com
dois casos pontuais de analise automatizada. **Deep Blue nao aprendeu.**

### 2.2 Por que AlphaZero, Leela e Stockfish-NNUE funcionam

- **Silver, D. et al. "A general reinforcement learning algorithm that masters chess, shogi, and Go
  through self-play". *Science* 362(6419):1140-1144, 7 de dezembro de 2018.**
  DOI `10.1126/science.aar6404` — **`[nao verificado]`**: https://www.science.org/doi/10.1126/science.aar6404
  retorna HTTP 403; registro bibliografico conferido via Crossref, texto nao aberto.
  Conteudo conferido no preprint aberto: **arXiv:1712.01815** `[abstract]`
  https://arxiv.org/abs/1712.01815 — "Starting from random play, and given no domain knowledge
  except the game rules, AlphaZero achieved within 24 hours a superhuman level of play".
- Antecedentes, ambos **`[nao verificado]`** (apenas Crossref): Silver, D. et al. "Mastering the
  game of Go with deep neural networks and tree search", *Nature* 529(7587):484-489, 2016, DOI
  `10.1038/nature16961`, https://doi.org/10.1038/nature16961 ; Silver, D. et al. "Mastering the
  game of Go without human knowledge", *Nature* 550(7676):354-359, 2017, DOI `10.1038/nature24270`,
  https://doi.org/10.1038/nature24270
- **NNUE, fonte primaria real: Nasu, Yu. "NNUE: Efficiently Updatable Neural-Network-based
  Evaluation Functions for Computer Shogi". Ziosoft Computer Shogi Club, 28 de abril de 2018,
  documento de apelo do 28o World Computer Shogi Championship.** Texto lido (japones com resumo em
  ingles):
  https://www.apply.computer-shogi.org/wcsc28/appeal/the_end_of_genesis_T.N.K.evolution_turbo_type_D/nnue.pdf
  Do resumo: "designed to run efficiently on CPU using various acceleration techniques, including
  **incremental computation**". NNUE e uma avaliacao nao-linear barata o bastante para viver
  **dentro** da busca alfa-beta. Nao substitui a busca; a alimenta.
- **Evidencia quantitativa de que a busca ainda carrega parte do desempenho: Ruoss, A. et al.
  "Amortized Planning with Large-Scale Transformers: A Case Study on Chess". arXiv:2402.04494
  (v1 em 7 de fevereiro de 2024; v2 em 21 de outubro de 2024).** `[abstract]`
  https://arxiv.org/abs/2402.04494 — transformers de ate 270 milhoes de parametros, treinados em
  15 bilhoes de anotacoes do Stockfish 16, **sem busca explicita**, atingem 2895 de Elo blitz no
  Lichess; ainda assim, "perfect distillation is still beyond reach".
  (Nota: o titulo da v1 era "Grandmaster-Level Chess Without Search"; a v2 renomeou o trabalho.)

O padrao comum e um so: **busca somada a funcao de avaliacao**. Deep Blue: busca enorme, avaliacao
manual. AlphaZero: busca menor (MCTS), avaliacao aprendida. NNUE: busca classica, avaliacao
aprendida barata. Ruoss et al.: avaliacao aprendida grande, sem busca — forte, mas nao fecha a
conta.

### 2.3 O que NAO transfere para Factorio

| Propriedade do xadrez | Situacao em Factorio | Consequencia |
|---|---|---|
| Self-play de soma zero | Nao ha adversario; o ambiente e fixo | Campeao vs. desafiante em ambiente fixo **nao e** self-play; e uma estrategia evolutiva (1+lambda) com nome emprestado, e nao herda nenhuma propriedade do self-play |
| Avaliacao estatica de posicao | Valor de um estado = throughput em regime, funcao de razoes de receita, taxas de inserter, capacidade de esteira, energia e tempo de acomodacao | Exige simulacao temporal; o FLE mede janela de 60 s justamente por isso |
| Espaco de acao pequeno e enumeravel (~35 lances) | (posicao em grid) x (tipo) x (rotacao) x (receita) x composicao; uma jogada util e um programa | Nao enumeravel; exige amostragem de acoes |
| Terminacao natural | PS explicitamente nao-limitado | Muda o que "vencer" significa; nao ha estado final de referencia |
| Reversibilidade barata | Desmontar custa tempo de jogo e recursos | O problema pertence a familia de reconfiguracao com custo, nao de busca em arvore de jogo |

### 2.4 O que transfere

1. **Busca guiada por avaliacao aprendida** — o nucleo comum de Deep Blue a AlphaZero.
2. **MCTS em dominio de um jogador.** **Kocsis, L.; Szepesvari, C. "Bandit Based Monte-Carlo
   Planning" (UCT), ECML 2006, LNCS, pp. 282-293**, DOI `10.1007/11871842_29` **`[nao verificado]`**
   https://doi.org/10.1007/11871842_29 ; **Browne, C. B. et al. "A Survey of Monte Carlo Tree Search
   Methods", *IEEE Trans. on Computational Intelligence and AI in Games* 4(1):1-43, 2012**, DOI
   `10.1109/TCIAIG.2012.2186810` **`[nao verificado]`** https://doi.org/10.1109/TCIAIG.2012.2186810
   Prova de conceito direta em otimizacao: AlphaDev formulou descoberta de algoritmo de ordenacao
   como "a single-player game" (secao 3.4).
3. **Curriculo.** **Bengio, Y.; Louradour, J.; Collobert, R.; Weston, J. "Curriculum learning",
   ICML 2009, pp. 41-48**, DOI `10.1145/1553374.1553380` **`[nao verificado]`**
   https://doi.org/10.1145/1553374.1553380
4. **Modelo aprendido usado dentro do planejamento** (secao 3.1 e 3.2).

Analogia melhor que xadrez, para espaco de acao combinatorio e horizonte longo: **Vinyals, O. et al.
"Grandmaster level in StarCraft II using multi-agent reinforcement learning", *Nature*
575(7782):350-354, 2019**, DOI `10.1038/s41586-019-1724-z` **`[nao verificado]`**
https://doi.org/10.1038/s41586-019-1724-z — continua sendo competitivo, e a parte de construir base
nunca foi resolvida isoladamente.

---

## 3. Familias de metodo aplicaveis

Visao comparativa antes do detalhe:

| Familia | O que exige | Onde costuma falhar | Como se avalia honestamente |
|---|---|---|---|
| Busca com modelo aprendido (MuZero) | Simulador rapido e reinicializavel; recompensa densa; compute alto | Simulacao cara torna a arvore rasa e o MCTS degenera em politica gulosa | Desempenho vs. orcamento de simulacoes por decisao; curva plana = busca inutil |
| Model-based RL / world models (Dreamer) | Sinal de recompensa; modelo usado no planejamento | Erro de predicao baixo nao implica controle bom | Bater persistencia em holdout rolante **e** melhorar a tarefa com o modelo ligado vs. desligado |
| Quality-diversity (MAP-Elites, POET) | Descritor de comportamento explicito | Descritor mal escolhido gera diversidade irrelevante; avaliacao ruidosa enche o arquivo de sorte | Cobertura do arquivo e QD-score ao longo do tempo, nao so o melhor individuo |
| Sintese de programas com LLM (FunSearch, Voyager) | Avaliador automatico, externo e barato | Sem avaliador confiavel, vira geracao de plausibilidade | Metrica externa verificavel; taxa de propostas validas e de melhorias aceitas |
| Otimizacao de layout e roteamento (QAP, PathFinder) | Objetivo mensuravel; modelo do grid e das capacidades | A* sequencial e guloso e dependente de ordem | Comparacao com o solver de referencia, nao com versao enfraquecida dele |
| Planejamento hierarquico (HTN, TAMP) | Dominio escrito a mao (metodos e operadores) | Decomposicao simbolica invivavel no nivel geometrico (downward refinement) | Taxa de sucesso por nivel da hierarquia; ablacao por metodo |

### 3.1 Busca com modelo aprendido (MuZero e MCTS nao-adversarial)

**Schrittwieser, J. et al. "Mastering Atari, Go, chess and shogi by planning with a learned model".
*Nature* 588:604-609, 23 de dezembro de 2020.** DOI `10.1038/s41586-020-03051-4`
https://www.nature.com/articles/s41586-020-03051-4 (registro bibliografico conferido na pagina do
editor; conteudo lido no preprint). Preprint: **arXiv:1911.08265** `[abstract]`
https://arxiv.org/abs/1911.08265

Ponto tecnico que importa mais aqui: "MuZero learns a model that, when applied iteratively,
predicts the quantities **most directly relevant to planning: the reward, the action-selection
policy, and the value function**". O modelo **nao** preve observacoes. Isso e o oposto de um world
model treinado para reconstruir estado.

**Hubert, T. et al. "Learning and Planning in Complex Action Spaces" (Sampled MuZero),
arXiv:2104.06303, ICML 2021.** `[abstract]` https://arxiv.org/abs/2104.06303 — "Many important
real-world problems have action spaces that are high-dimensional, continuous or both, making full
enumeration of all possible actions infeasible. Instead, only small subsets of actions can be
sampled". E o mecanismo que torna MuZero aplicavel a um espaco tipo Factorio.

*Critica.* O gargalo pratico e o custo de simulacao. Com 68 ops/s (numero do proprio FLE, secao
1.1), o orcamento de simulacoes por decisao e pequeno, e uma arvore rasa nao entrega o que a
familia promete. O teste diagnostico e barato e conclusivo: medir desempenho contra orcamento de
simulacoes. Se a curva for plana, a busca nao esta fazendo trabalho.

### 3.2 Model-based RL e world models

- **Hafner, D.; Lillicrap, T.; Norouzi, M.; Ba, J. "Mastering Atari with Discrete World Models"
  (DreamerV2), arXiv:2010.02193, ICLR 2021.** **`[nao verificado]`** (pagina arXiv aberta; registro
  bibliografico e nota de publicacao ICLR 2021 conferidos; resumo nao lido)
  https://arxiv.org/abs/2010.02193
- **Hafner, D.; Pasukonis, J.; Ba, J.; Lillicrap, T. "Mastering diverse control tasks through world
  models" (DreamerV3), *Nature* 640:647-653, 2 de abril de 2025.** DOI `10.1038/s41586-025-08744-2`
  https://www.nature.com/articles/s41586-025-08744-2 (registro conferido na pagina do editor).
  Preprint: **arXiv:2301.04104** `[abstract]` https://arxiv.org/abs/2301.04104 — "Dreamer learns a
  model of the environment and **improves its behavior by imagining future scenarios**... the first
  algorithm to collect diamonds in Minecraft from scratch without human data or curricula".

**Criterio correto para aceitar um world model — tres testes, todos necessarios:**

1. **Bater o baseline ingenuo em holdout.** **Hyndman, R. J.; Athanasopoulos, G. "Forecasting:
   Principles and Practice", 3a edicao, OTexts, 2021**, secao 5.2:
   "For naive forecasts, we simply set all forecasts to be the value of the last observation";
   e "in many cases, these methods will serve as benchmarks rather than the method of choice" — se
   o metodo novo nao supera os simples, "the new method is not worth considering".
   https://otexts.com/fpp3/simple-methods.html
   O holdout tem de ser de **origem rolante**, nao split aleatorio: "no future observations can be
   used in constructing the forecast". https://otexts.com/fpp3/tscv.html
   Que benchmarks simples derrotem metodos sofisticados nao e exotico: **Makridakis, S.; Spiliotis,
   E.; Assimakopoulos, V. "The M4 Competition: 100,000 time series and 61 forecasting methods",
   *International Journal of Forecasting* 36(1):54-74, 2020**, DOI
   `10.1016/j.ijforecast.2019.04.014` **`[nao verificado]`**
   https://doi.org/10.1016/j.ijforecast.2019.04.014
2. **Ser usado no controle.** Um modelo que nunca e carregado em inferencia nao e um world model;
   e um experimento de previsao arquivado. Em MuZero e em Dreamer o modelo **e** o planejador.
3. **Melhorar a tarefa, nao so o erro de predicao.** **Lambert, N.; Amos, B.; Yadan, O.; Calandra,
   R. "Objective Mismatch in Model-based Reinforcement Learning", arXiv:2002.04523, L4DC 2020.**
   `[abstract]` https://arxiv.org/abs/2002.04523 — "we demonstrate that the likelihood of one-step
   ahead predictions is **not always correlated with control performance**". O criterio corta nos
   dois sentidos: um modelo que *ganhasse* da persistencia ainda assim poderia nao melhorar o
   controle.

Fragilidade documentada da familia: **Wang, T. et al. "Benchmarking Model-Based Reinforcement
Learning", arXiv:1907.02057** **`[nao verificado]`** https://arxiv.org/abs/1907.02057

### 3.3 Quality-diversity e open-endedness

- **Mouret, J.-B.; Clune, J. "Illuminating search spaces by mapping elites" (MAP-Elites),
  arXiv:1504.04909, 20 de abril de 2015.** `[abstract]` https://arxiv.org/abs/1504.04909 — arquivo
  de elites indexado por dimensoes de variacao escolhidas pelo usuario; "because MAP-Elites
  explores more of the search space, it also tends to find a better overall solution than
  state-of-the-art search algorithms".
- **Cully, A.; Clune, J.; Tarapore, D.; Mouret, J.-B. "Robots that can adapt like animals",
  *Nature* 521:503-507, 27 de maio de 2015.** DOI `10.1038/nature14422` `[abstract]`
  https://www.nature.com/articles/nature14422 — o arquivo QD permite recuperacao em menos de dois
  minutos apos dano, "without requiring self-diagnosis or pre-specified contingency plans".
- **Lehman, J.; Stanley, K. O. "Abandoning Objectives: Evolution Through the Search for Novelty
  Alone", *Evolutionary Computation* 19(2):189-223, 2011.** DOI `10.1162/EVCO_a_00025`. Texto lido
  em copia academica: https://www.cs.swarthmore.edu/~meeden/DevelopmentalRobotics/lehman_ecj11.pdf
  (MIT Press retorna HTTP 403.) Do texto: "Objective functions themselves may **actively misdirect
  search towards dead ends**".
- **Wang, R.; Lehman, J.; Clune, J.; Stanley, K. O. "Paired Open-Ended Trailblazer (POET)",
  arXiv:1901.01753, 2019.** `[abstract]` https://arxiv.org/abs/1901.01753 — gera os problemas junto
  com as solucoes, com **transferencia entre nichos**; produz comportamentos "which cannot be solved
  by direct optimization alone, or even through a direct-path curriculum-building control
  algorithm". Continuacao: **"Enhanced POET", arXiv:2003.08536, ICML 2020** **`[nao verificado]`**
  https://arxiv.org/abs/2003.08536
- **Cully, A.; Demiris, Y. "Quality and Diversity Optimization: A Unifying Modular Framework",
  *IEEE TEVC* 22(2):245-259, 2018**, DOI `10.1109/TEVC.2017.2704781` **`[nao verificado]`**
  https://doi.org/10.1109/TEVC.2017.2704781
- **Fontaine, M. C.; Togelius, J.; Nikolaidis, S.; Hoover, A. K. "Covariance Matrix Adaptation for
  the Rapid Illumination of Behavior Space" (CMA-ME), arXiv:1912.02400, GECCO 2020**
  **`[nao verificado]`** https://arxiv.org/abs/1912.02400
- **Hughes, E. et al. "Open-Endedness is Essential for Artificial Superhuman Intelligence",
  arXiv:2406.04268, 2024** **`[nao verificado]`** https://arxiv.org/abs/2406.04268
- **Jiang, M.; Grefenstette, E.; Rocktaschel, T. "Prioritized Level Replay", arXiv:2010.03934**
  **`[nao verificado]`** https://arxiv.org/abs/2010.03934 — curriculo por priorizacao de niveis
  existentes; mais barato que POET.

*Critica.* Exige um **descritor de comportamento**; para fabrica: area ocupada, numero de esteiras,
throughput, consumo de energia, poluicao, profundidade da cadeia. Sem descritor, MAP-Elites nao
existe. Com avaliacao ruidosa, o arquivo se enche de sorte — ver **Jin, Y.; Branke, J.
"Evolutionary Optimization in Uncertain Environments - A Survey", *IEEE TEVC* 9(3):303-317, 2005**,
DOI `10.1109/TEVC.2005.846356` **`[nao verificado]`** https://doi.org/10.1109/TEVC.2005.846356

### 3.4 Sintese de programas e LLM como gerador de codigo de construcao

- **Wang, G. et al. "Voyager: An Open-Ended Embodied Agent with Large Language Models",
  arXiv:2305.16291, 2023.** `[abstract]` https://arxiv.org/abs/2305.16291 — tres componentes:
  curriculo automatico, **biblioteca crescente de habilidades em codigo executavel** e prompting
  iterativo com feedback de erro e auto-verificacao. Mede: "3.3x more unique items, travels 2.3x
  longer distances, and unlocks key tech tree milestones up to 15.3x faster than prior SOTA", mais
  transferencia para mundo novo.
- **Romera-Paredes, B. et al. "Mathematical discoveries from program search with large language
  models" (FunSearch), *Nature* 625:468-475, 2024.** DOI `10.1038/s41586-023-06924-6` `[abstract]`
  https://www.nature.com/articles/s41586-023-06924-6 — LLM **pareado com um avaliador sistematico**;
  mede resultados no problema de cap sets e em online bin packing contra heuristicas de uso
  corrente. Frase que define o metodo: "FunSearch searches for **programs that describe how to solve
  a problem, rather than what the solution is**".
- **Mankowitz, D. J. et al. "Faster sorting algorithms discovered using deep reinforcement learning"
  (AlphaDev), *Nature* 618:257-263, 2023.** DOI `10.1038/s41586-023-06004-9` `[abstract]`
  https://www.nature.com/articles/s41586-023-06004-9 — formulado como "a single-player game";
  criterio de aceite objetivo e externo (latencia medida), com o codigo integrado a biblioteca
  padrao de sort do LLVM.
- **Novikov, A. et al. "AlphaEvolve: A coding agent for scientific and algorithmic discovery",
  arXiv:2506.13131, 16 de junho de 2025.** `[abstract]` https://arxiv.org/abs/2506.13131 — pipeline
  evolutivo de LLMs recebendo feedback de "one or more **evaluators**"; resultados verificaveis
  (multiplicacao de matrizes complexas 4x4 com 48 multiplicacoes escalares; escalonamento de
  datacenter).
- **Lehman, J. et al. "Evolution through Large Models", arXiv:2206.08896, 2022**
  **`[nao verificado]`** https://arxiv.org/abs/2206.08896 ; **Ma, Y. J. et al. "Eureka: Human-Level
  Reward Design via Coding Large Language Models", arXiv:2310.12931, ICLR 2024**
  **`[nao verificado]`** https://arxiv.org/abs/2310.12931

*Critica.* O que todos medem e uma **metrica externa e automatica**, nunca a opiniao do proprio LLM.
O avaliador e o componente que faz o metodo funcionar; o LLM e o operador de mutacao. Mutar
*hiperparametros* com LLM, em vez de mutar *o programa de construcao*, descarta a vantagem inteira
da familia: o objeto que FunSearch e AlphaEvolve evoluem e codigo.

### 3.5 Otimizacao combinatoria de layout e roteamento multi-caminho

**Layout de fabrica.** O problema canonico e o *quadratic assignment problem*: **Koopmans, T. C.;
Beckmann, M. "Assignment Problems and the Location of Economic Activities", *Econometrica*
25(1):53-76, 1957**, DOI `10.2307/1907742` **`[nao verificado]`** https://doi.org/10.2307/1907742
E NP-dificil e **nao admite aproximacao de razao constante a menos que P=NP**: **Sahni, S.;
Gonzalez, T. "P-Complete Approximation Problems", *Journal of the ACM* 23(3):555-565, 1976**, DOI
`10.1145/321958.321975` **`[nao verificado]`** https://doi.org/10.1145/321958.321975
Panorama: **Drira, A.; Pierreval, H.; Hajri-Gabouj, S. "Facility layout problems: A survey",
*Annual Reviews in Control* 31(2):255-267, 2007**, DOI `10.1016/j.arcontrol.2007.04.001`
**`[nao verificado]`** https://doi.org/10.1016/j.arcontrol.2007.04.001

**Roteamento com multiplas rotas competindo por espaco — o analogo correto de esteiras, canos e
trilhos.**

**McMurchie, L.; Ebeling, C. "PathFinder: A Negotiation-Based Performance-Driven Router for FPGAs",
FPGA'95, ACM, pp. 111-117**, DOI `10.1145/201310.201328`. Texto lido:
https://www.cecs.uci.edu/~papers/compendium94-03/papers/1995/fpga95/pdffiles/6a.pdf
(ACM DL retorna HTTP 403.) Do artigo:

> "Routability is achieved by forcing signals to **negotiate** for a resource and thereby determine
> which signal needs the resource most. Delay is minimized by allowing the more critical signals a
> greater say in this negotiation."

E o diagnostico exato do problema:

> "the solution to the entire routing problem requires the **simultaneous** solution to two
> interacting and competing subproblems"

**Por que A* puro e insuficiente para multiplas rotas.** A* resolve *uma* rota otima num grafo com
custos fixos. Com N rotas roteadas em sequencia, o custo de um recurso deixa de ser fixo: depende de
quem ja o ocupou. O procedimento vira **guloso e dependente da ordem** — a primeira rota pega o
caminho curto e pode tornar as seguintes inviaveis ou longas, e nada no algoritmo permite revisar
essa decisao. PathFinder resolve isso permitindo sobre-utilizacao temporaria e fazendo o custo do
recurso crescer com a congestao presente e com o historico acumulado, iterando ate nao haver
conflito.

A dureza e formal, nao impressao:
- **Even, S.; Itai, A.; Shamir, A. "On the Complexity of Timetable and Multicommodity Flow
  Problems", *SIAM Journal on Computing* 5(4):691-703, 1976**, DOI `10.1137/0205048`
  **`[nao verificado]`** https://doi.org/10.1137/0205048
- **Yu, J.; LaValle, S. M. "Structure and Intractability of Optimal Multi-Robot Path Planning on
  Graphs", AAAI 2013, pp. 1443-1449**, DOI `10.1609/aaai.v27i1.8541` **`[nao verificado]`**
  https://doi.org/10.1609/aaai.v27i1.8541
- Alternativa otima com resolucao de conflitos: **Sharon, G.; Stern, R.; Felner, A.; Sturtevant,
  N. R. "Conflict-based search for optimal multi-agent pathfinding", *Artificial Intelligence*
  219:40-66, 2015**, DOI `10.1016/j.artint.2014.11.006` **`[nao verificado]`**
  https://doi.org/10.1016/j.artint.2014.11.006
- Definicoes e benchmarks: **Stern, R. et al. "Multi-Agent Pathfinding: Definitions, Variants, and
  Benchmarks", arXiv:1906.08291, SoCS 2019** **`[nao verificado]`** https://arxiv.org/abs/1906.08291

Em Factorio ha restricoes que o A* geometrico nao modela e que Reid et al. (arXiv:2102.04871)
modelam explicitamente: capacidade e throughput por esteira, direcao e alcance de esteira
subterranea (entrada e saida na mesma linha ou coluna, mesma direcao), posicionamento de inserters,
razao correta de itens na saida.

**Aprender a rotear ou otimizar**: **Bengio, Y.; Lodi, A.; Prouvost, A. "Machine learning for
combinatorial optimization: A methodological tour d'horizon", *European Journal of Operational
Research* 290(2):405-421, 2021**, DOI `10.1016/j.ejor.2020.07.063` **`[nao verificado]`**
https://doi.org/10.1016/j.ejor.2020.07.063 ; **Kool, W.; van Hoof, H.; Welling, M. "Attention, Learn
to Solve Routing Problems!", arXiv:1803.08475, ICLR 2019** **`[nao verificado]`**
https://arxiv.org/abs/1803.08475
O padrao metodologico aqui e inflexivel: **comparar com o solver ou heuristica de referencia, nunca
com versoes enfraquecidas dele**.

### 3.6 Planejamento hierarquico (HTN) e task-and-motion planning

- **Erol, K.; Hendler, J.; Nau, D. S. "HTN Planning: Complexity and Expressivity", AAAI-94.**
  Texto lido: https://www.cs.umd.edu/~nau/papers/erol1994htn.pdf — "Most practical work on AI
  planning systems during the last fifteen years has been based on hierarchical task network (HTN)
  decomposition"; o artigo estabelece como a complexidade varia com as condicoes impostas as redes
  de tarefas.
- **Nau, D. S. et al. "SHOP2: An HTN Planning System", *JAIR* 20:379-404, 2003**
  **`[nao verificado]`** (pagina arXiv aberta, registro conferido; conteudo nao lido)
  https://arxiv.org/abs/1106.4869
- **Georgievski, I.; Aiello, M. "HTN planning: Overview, comparison, and beyond", *Artificial
  Intelligence* 222:124-156, 2015**, DOI `10.1016/j.artint.2015.02.002` **`[nao verificado]`**
  https://doi.org/10.1016/j.artint.2015.02.002
- **Garrett, C. R. et al. "Integrated Task and Motion Planning", *Annual Review of Control,
  Robotics, and Autonomous Systems*, vol. 4, 2021; arXiv:2010.01083** **`[nao verificado]`**
  https://arxiv.org/abs/2010.01083

*Por que e o encaixe natural aqui.* Factorio traz a hierarquia de graca na arvore de receitas:
"produzir X placas/s" decompoe em receitas, secoes, posicionamento e roteamento. TAMP e literalmente
o casamento de decisao simbolica (que secao construir) com viabilidade geometrica (cabe e conecta),
que e o modo de falha numero 1 documentado no FLE.

---

## 4. O problema da destruicao e reconstrucao

A pergunta "quando destruir para reconstruir melhor" tem nome na literatura de otimizacao: **large
neighborhood search** (LNS), tambem chamada *ruin-and-recreate* ou *destroy-and-repair*. O ponto de
partida e que buscas locais com vizinhanca pequena ficam presas; destruir uma **parte grande** da
solucao e reconstrui-la com um metodo forte explora uma vizinhanca exponencialmente maior a custo
controlado.

Fontes primarias — todas com registro conferido via Crossref e **texto integral nao aberto**:

| Referencia | Venue | Contribuicao | Marca |
|---|---|---|---|
| Shaw, P. "Using Constraint Programming and Local Search Methods to Solve Vehicle Routing Problems" | CP-98, LNCS, pp. 417-431, 1998, DOI `10.1007/3-540-49481-2_30` | Origem do LNS | **`[nao verificado]`** |
| Schrimpf, G.; Schneider, J.; Stamm-Wilbrandt, H.; Dueck, G. "Record Breaking Optimization Results Using the Ruin and Recreate Principle" | *J. Computational Physics* 159(2):139-171, 2000, DOI `10.1006/jcph.1999.6413` | Formulacao explicita de arruinar e recriar | **`[nao verificado]`** |
| Ropke, S.; Pisinger, D. "An Adaptive Large Neighborhood Search Heuristic for the Pickup and Delivery Problem with Time Windows" | *Transportation Science* 40(4):455-472, 2006, DOI `10.1287/trsc.1050.0135` | ALNS: varios operadores de destruicao e reparo, com pesos adaptados ao desempenho recente | **`[nao verificado]`** |
| Pisinger, D.; Ropke, S. "Large Neighborhood Search" | *Handbook of Metaheuristics*, Springer, pp. 399-419, 2010, DOI `10.1007/978-1-4419-1665-5_13` | Tratamento de referencia | **`[nao verificado]`** |
| Hottung, A.; Tierney, K. "Neural Large Neighborhood Search for the Capacitated Vehicle Routing Problem" | arXiv:1911.09539, ECAI 2020 | Reparo aprendido | **`[nao verificado]`** |
| Sonnerat, N.; Wang, P.; Ktena, I.; Bartunov, S.; Nair, V. "Learning a Large Neighborhood Search Algorithm for Mixed Integer Programs" | arXiv:2107.10201 | Aprende **qual subconjunto destruir** | **`[nao verificado]`** |

URLs: https://doi.org/10.1007/3-540-49481-2_30 · https://doi.org/10.1006/jcph.1999.6413 ·
https://doi.org/10.1287/trsc.1050.0135 · https://doi.org/10.1007/978-1-4419-1665-5_13 ·
https://arxiv.org/abs/1911.09539 · https://arxiv.org/abs/2107.10201

Reconfiguracao com custo, no dominio de fabricas reais — ambas **`[nao verificado]`**:
**Rosenblatt, M. J. "The Dynamics of Plant Layout", *Management Science* 32(1):76-86, 1986**, DOI
`10.1287/mnsc.32.1.76` https://doi.org/10.1287/mnsc.32.1.76 — o problema de layout **dinamico**:
quando vale rearranjar, dado que rearranjar custa. **Koren, Y. et al. "Reconfigurable Manufacturing
Systems", *CIRP Annals* 48(2):527-540, 1999**, DOI `10.1016/S0007-8506(07)63232-6`
https://doi.org/10.1016/S0007-8506(07)63232-6

### 4.1 Como aplicar a uma fabrica em Factorio

1. **Representar a fabrica como solucao explicita**, e nao como sequencia de chamadas de API: um
   blueprint (conjunto de entidades com posicao, tipo, rotacao e receita) mais o grafo de fluxo. Sem
   esse objeto, "destruir parte da solucao" nao tem referente.
2. **Funcao objetivo mensuravel**: throughput sustentado do item-alvo numa janela de holdout (o FLE
   ja define 16/s solidos, 250/s fluidos, 60 segundos), penalizada por area, numero de esteiras,
   energia e poluicao.
3. **Operadores de destruicao** — o conjunto, nao um so, conforme ALNS: raio geometrico em torno de
   um ponto; uma secao inteira de producao; o conjunto de rotas que atravessa o corredor mais
   congestionado; as entidades sub-utilizadas (throughput real muito abaixo do nominal); remocao
   aleatoria com vies no gargalo identificado por `production_stats`.
4. **Reparo**: dimensionar maquinas por razao de receita (calculo exato, nao busca) e rotear com
   congestao negociada estilo PathFinder, nao A* sequencial.
5. **Criterio de aceitacao explicito**, incluindo o **custo de reconstrucao** (tempo de fabrica
   parada e recursos). E aqui que mora a resposta a pergunta "quando destruir": aceita-se a
   destruicao quando o ganho esperado de throughput, descontado o tempo parado, supera o valor
   atual — exatamente a estrutura de Rosenblatt (1986). O criterio tipo simulated annealing de
   Schrimpf et al. permite aceitar pioras pequenas para escapar de otimos locais; um criterio
   estritamente guloso produz congelamento.
6. **Aprender o que destruir** so depois que o LNS manual funcionar, seguindo Sonnerat et al.

Conexao com o estado do projeto: "campeao congelado por 13 geracoes" e o sintoma classico de
vizinhanca pequena demais somada a criterio de aceitacao estritamente elitista. LNS ataca a
vizinhanca; o criterio de aceitacao ataca o elitismo; quality-diversity (secao 3.3) ataca o colapso
para um unico campeao.

---

## 5. Metodologia de avaliacao

### 5.1 O que a literatura exige para afirmar "esta aprendendo"

| Exigencia | Referencia | Marca |
|---|---|---|
| Baseline simples e correto; se o metodo novo nao o supera, nao vale considerar | Hyndman & Athanasopoulos (2021), secao 5.2 | lida |
| Comparacao com o solver de referencia em otimizacao combinatoria | Bengio, Lodi & Prouvost (2021) | **`[nao verificado]`** |
| Multiplas seeds e reporte de variancia | Henderson et al., AAAI 2018 | `[abstract]` |
| Estimativas por intervalo, perfis de desempenho, media interquartil (IQM) | Agarwal et al., NeurIPS 2021 | `[abstract]` |
| Numero de seeds dimensionado por analise de poder | Colas, Sigaud & Oudeyer (2018) | `[abstract]` |
| Barras de erro em avaliacoes de modelos de linguagem | Miller (2024) | **`[nao verificado]`** |
| Ablacao por componente, nao narrativa | Lipton & Steinhardt (2018) | **`[nao verificado]`** |
| Holdout de origem rolante para series temporais | Hyndman & Athanasopoulos (2021), secao 5.10 | lida |
| Metrica e proxy, nao objetivo; otimizar proxy demais piora o objetivo | Karwowski et al. (2023) | `[abstract]` |
| Avaliacao ruidosa exige reamostragem e reavaliacao | Jin & Branke (2005) | **`[nao verificado]`** |
| Bandit pressupoe gap real entre bracos | Auer, Cesa-Bianchi & Fischer (2002) | **`[nao verificado]`** |

Referencias completas:

- **Henderson, P.; Islam, R.; Bachman, P.; Pineau, J.; Precup, D.; Meger, D. "Deep Reinforcement
  Learning that Matters", AAAI 2018; arXiv:1709.06560.** `[abstract]`
  https://arxiv.org/abs/1709.06560 — "non-determinism in standard benchmark environments, combined
  with variance intrinsic to the methods, can make reported results tough to interpret... Without
  significance metrics and tighter standardization of experimental reporting, it is difficult to
  determine whether improvements over the prior state-of-the-art are meaningful".
- **Agarwal, R.; Schwarzer, M.; Castro, P. S.; Courville, A.; Bellemare, M. G. "Deep Reinforcement
  Learning at the Edge of the Statistical Precipice", NeurIPS 2021 (Outstanding Paper);
  arXiv:2108.13264.** `[abstract]` https://arxiv.org/abs/2108.13264 — recomenda **intervalos de
  confianca agregados**, **perfis de desempenho** e a **media interquartil (IQM)**; biblioteca
  `rliable`.
- **Colas, C.; Sigaud, O.; Oudeyer, P.-Y. "How Many Random Seeds? Statistical Power Analysis in Deep
  Reinforcement Learning Experiments", arXiv:1806.08295.** `[abstract]`
  https://arxiv.org/abs/1806.08295
- **Miller, E. "Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations",
  arXiv:2411.00640, 2024.** **`[nao verificado]`** https://arxiv.org/abs/2411.00640
- **Lipton, Z. C.; Steinhardt, J. "Troubling Trends in Machine Learning Scholarship",
  arXiv:1807.03341, ICML 2018 Debates.** **`[nao verificado]`** https://arxiv.org/abs/1807.03341
- **Karwowski, J. et al. "Goodhart's Law in Reinforcement Learning", arXiv:2310.09144 (ICLR 2024).**
  `[abstract]` https://arxiv.org/abs/2310.09144 — "increasing optimisation of an imperfect proxy
  beyond some critical point decreases performance on the true objective".
- **Auer, P.; Cesa-Bianchi, N.; Fischer, P. "Finite-time Analysis of the Multiarmed Bandit Problem",
  *Machine Learning* 47:235-256, 2002**, DOI `10.1023/A:1013689704352` **`[nao verificado]`**
  (pagina do editor aberta; registro conferido; conteudo nao lido)
  https://link.springer.com/article/10.1023/A:1013689704352
- **Lattimore, T.; Szepesvari, C. "Bandit Algorithms", Cambridge University Press, 2020**, edicao
  online gratuita. **`[nao verificado]`** (capa e sumario conferidos; capitulos nao lidos)
  https://tor-lattimore.com/downloads/book/book.pdf

Ponto tecnico sobre bandits que sustenta a secao seguinte: as garantias do UCB1 sao sobre
**arrependimento**. Se todos os bracos tem a mesma media, o arrependimento e trivialmente zero e a
"selecao" carrega zero informacao. Um UCB1 operando sobre bracos equivalentes e indistinguivel de
um gerador aleatorio.

### 5.2 Mapeamento dos fatos medidos para praticas violadas

Condicionado a correcao das medicoes declaradas (ver ressalva 2 no cabecalho — **nao reproduzidas
pelo autor deste documento**):

| Fato declarado | Pratica violada | Referencia |
|---|---|---|
| Parametros mutados com r=0,031 (n=30) sobre o resultado | Ausencia de ablacao e de analise de sensibilidade antes de construir a maquinaria evolutiva sobre eles; n=30 tambem e pequeno para afirmar ausencia de efeito com confianca | Lipton & Steinhardt (2018); Colas et al. (2018) |
| Campeao congelado na geracao 6; 13 geracoes rejeitadas | Selecao elitista sobre avaliacao ruidosa, sem reavaliacao do campeao nem multiplas amostras por individuo; reportar o melhor individuo esconde o colapso de diversidade | Jin & Branke (2005); Mouret & Clune (2015) |
| Bandit de posicionamento com teste de permutacao p=0,62 | O teste esta correto e o resultado e conclusivo: nao ha gap entre bracos. UCB1 sobre bracos equivalentes nao seleciona nada | Auer et al. (2002); Lattimore & Szepesvari (2020) |
| World model perde para persistencia em 4/4 folds e nunca e carregado em inferencia | Falha no benchmark ingenuo (condicao necessaria) **e** ausencia de uso no controle (condicao que define o artefato). Dois criterios independentes reprovados | Hyndman & Athanasopoulos (2021, 5.2); Lambert et al. (2020); Schrittwieser et al. (2020) |
| Spatial policy com 7 empates e 1 pior em 8 rotas contra A* | O baseline forte domina; o componente aprendido nao tem justificativa no regime testado. Nao e "ainda nao aprendeu", e "nao ha ganho a capturar em rota unica" — o ganho estaria em **multiplas** rotas competindo, que nao foram testadas | Bengio, Lodi & Prouvost (2021); McMurchie & Ebeling (1995) |
| Metricas de producao constantes (138/92/10) com chamadas manuais quadruplicadas | A metrica e identidade algebrica do roteiro, nao observacao de aprendizado; e contabiliza logistica manual como se fosse automacao | Karwowski et al. (2023); FLE (lab-play com holdout de 60 s e o instrumento correto) |
| Cobertura fisica mineracao->processamento de 16,7% | A tarefa declarada (rede de esteiras) nao e a tarefa medida; sem medir a tarefa declarada, nenhuma conclusao sobre ela se sustenta | FLE, insight 2 |

### 5.3 Duas observacoes de fundo

**Primeira.** O conjunto dos sete achados tem uma causa comum, nao sete causas. Todos os componentes
aprendidos foram medidos contra nada ou contra si mesmos, e nenhum foi medido contra aquilo que o
substituiria. A ordem correta e: instrumento de medida, depois baseline, depois componente. O
sistema foi construido na ordem inversa, e a auditoria e o instrumento chegando por ultimo.

**Segunda.** Os resultados negativos ja obtidos sao caros de produzir e tem valor. Quatro folds
contra persistencia, um teste de permutacao e uma correlacao com n=30 sao mais evidencia
metodologica do que a maior parte do que se publica na area apresenta. O erro seria reagir a eles
tentando salvar os componentes.

---

## 6. Recomendacao priorizada

Ordenada por retorno sobre esforco. Cada item tem a referencia que o ancora.

| # | Acao | Esforco | Retorno | Ancora |
|---|---|---|---|---|
| 1 | Metrica de aceite = throughput sustentado em holdout, sem intervencao | baixo | maximo | FLE; Karwowski et al. |
| 2 | Desligar os tres componentes ja refutados pela medicao | baixo | alto | Hyndman; Lambert; Lipton & Steinhardt |
| 3 | Roteamento com congestao negociada no lugar de A* sequencial | medio | alto | McMurchie & Ebeling; Even et al.; Reid et al. |
| 4 | LNS / ruin-and-recreate sobre o blueprint | medio | alto | Shaw; Schrimpf et al.; Ropke & Pisinger; Rosenblatt |
| 5 | Protocolo de avaliacao fixo antes do proximo experimento | baixo | alto | Agarwal et al.; Henderson et al.; Colas et al. |
| 6 | Decomposicao hierarquica explicita (HTN/TAMP) | medio | medio | Erol et al.; Nau et al.; Garrett et al. |
| 7 | Arquivo quality-diversity no lugar de campeao unico | medio | medio | Mouret & Clune; Cully et al.; Wang et al. |
| 8 | LLM como gerador de programas de construcao, com avaliador no laco | medio | medio | Romera-Paredes et al.; Novikov et al.; Wang et al. |
| 9 | Busca com modelo aprendido (MuZero/Dreamer) | alto | incerto | Schrittwieser et al.; Hubert et al.; Hafner et al. |

**1. Trocar a metrica de aceite por throughput sustentado em janela de holdout, sem intervencao do
agente.** Antes de qualquer outra mudanca. O teste: durante N segundos de jogo o agente nao emite
nenhuma chamada; mede-se o throughput do item-alvo e a cobertura fisica mineracao->processamento.
Crafting manual e `insert_item` deixam de contar por construcao. E o que o FLE faz em lab-play
(16/s solidos, 250/s fluidos, 60 s). Sem isto, nenhum dos itens seguintes e verificavel.

**2. Desligar os tres componentes que a medicao ja refutou, e registrar o motivo.** Bandit de
posicionamento (p=0,62), world model (perde para persistencia e nao e usado) e spatial policy (nao
supera A*). Nao e desperdicio: e o resultado do experimento. Manter codigo que nao passa no proprio
teste contamina toda medicao futura, porque cria a impressao de que o sistema tem capacidades que
nao tem.

**3. Substituir A* sequencial por roteamento com congestao negociada.** Maior ganho de capacidade
real e o mais bem resolvido pela literatura. Custo de celula = custo base x (1 + congestao
presente) x historico acumulado, permitindo sobre-utilizacao temporaria e iterando ate zero
conflito. Resolve esteiras, canos, energia e trilhos **no mesmo grid**, que e o problema real e que
A* por rota nao pode resolver por ser guloso e dependente de ordem.

**4. Trocar a evolucao por LNS / ruin-and-recreate sobre o blueprint.** Exige representar a fabrica
como solucao explicita. Varios operadores de destruicao com pesos adaptativos (ALNS), reparo por
calculo de razao de receita mais o roteamento do item 3, e criterio de aceitacao que contabiliza o
custo de reconstrucao. E a resposta direta ao requisito "saber quando destruir".

**5. Protocolo de avaliacao fixo antes do proximo experimento.** Minimo de 5 a 10 seeds por
configuracao; IQM com intervalo de confianca bootstrap; perfis de desempenho; ablacao obrigatoria
por componente (ligado/desligado, mesmas seeds); teste de permutacao como portao de entrada de
qualquer componente estocastico novo. Esforco baixo, e e o que impede a repeticao do ciclo.

**6. Introduzir decomposicao hierarquica explicita.** "Produzir X/s" -> receitas e razoes -> secoes
-> posicionamento -> roteamento, com viabilidade geometrica realimentando a decisao simbolica.
Ataca diretamente o modo de falha numero 1 do FLE (colocar entidades sem espaco para conexao).

**7. Trocar campeao unico por arquivo quality-diversity.** MAP-Elites indexado por descritores de
fabrica (area, numero de esteiras, throughput, energia, poluicao, profundidade da cadeia),
reportando cobertura e QD-score. E a correcao estrutural do campeao congelado: o arquivo nao
congela porque nao ha um unico slot a defender. Curriculo automatico (PLR, mais barato; POET, mais
ambicioso) se e quando o arquivo estagnar.

**8. Usar o LLM local como gerador e mutador de *programas de construcao*, com avaliador automatico
no laco — nao como mutador de hiperparametros.** E a licao comum de FunSearch, AlphaEvolve e
Voyager: o LLM propoe codigo, o avaliador externo decide, a biblioteca de habilidades acumula o que
passou. Com um modelo local pequeno a taxa de propostas validas sera baixa, o que e aceitavel desde
que o avaliador seja barato e rigoroso — FunSearch opera nesse regime.

**9. So entao considerar busca com modelo aprendido.** MuZero/Sampled MuZero ou Dreamer, com o
modelo **usado no planejamento** e aceito apenas por criterio downstream. Por ultimo
deliberadamente: o gargalo e o custo de simulacao do FLE (68 ops/s com o interpretador Python, numero
do proprio paper), que limita a profundidade de qualquer MCTS. Antes de investir, rodar o teste
barato: desempenho contra orcamento de simulacoes por decisao. Curva plana significa que a busca nao
trabalha e o investimento nao se paga.

### 6.1 Observacao final sobre a analogia do xadrez

Deep Blue nao aprendeu nada e venceu com busca de 200 milhoes de posicoes por segundo sobre uma
avaliacao ajustada a mao. AlphaZero aprendeu tudo, mas a pressao de melhoria vinha do adversario,
que Factorio nao tem. O caminho para Factorio nao e nenhum dos dois: e **busca sobre estrutura
explicita** — layout e roteamento como problemas de otimizacao com objetivo medido — com
aprendizado entrando onde houver ganho demonstravel sobre o solver de referencia. Os tres
componentes aprendidos do sistema atual foram colocados onde ja havia solver forte (A*) ou onde nao
havia sinal (bandit, world model). Realocar o aprendizado para onde os solvers classicos sao fracos
— escolher o que destruir, priorizar sub-problemas, ordenar rotas criticas — e a mudanca conceitual
que as referencias sustentam.

---

## 7. O que refutaria cada recomendacao

Cada item abaixo e a observacao que, se ocorrer, derruba a recomendacao correspondente. Sao criterios
de parada, nao formalidade.

| # | Refutado por |
|---|---|
| 1 | Se agentes que passam no holdout de throughput nao se distinguirem dos que falham em nenhuma outra metrica de interesse, o holdout nao esta medindo o que se supoe. Tambem: se o holdout for tao ruidoso entre seeds que o intervalo de confianca cubra o alvo inteiro, a metrica nao serve como portao |
| 2 | Se, ao religar qualquer dos tres componentes com o protocolo do item 5, a metrica do item 1 melhorar de forma significativa e replicavel, o componente nao estava refutado — a medicao anterior estava mal especificada |
| 3 | Se, num conjunto de cenarios com multiplas rotas competindo, o roteador com congestao negociada empatar com A* sequencial em comprimento total, taxa de conclusao e throughput, a complexidade adicional nao se paga. Tambem: se a fabrica-alvo nunca atingir densidade em que rotas competem, o problema nao existe nessa escala |
| 4 | Se a curva de melhoria do LNS ficar plana desde a primeira iteracao sobre fabricas reais — isto e, se nenhum operador de destruicao produzir reconstrucao melhor que a original — entao a solucao inicial ja e localmente otima e o gargalo esta em outro lugar (provavelmente na geracao inicial, nao na busca) |
| 5 | Nao e refutavel por resultado, apenas por custo: se o orcamento de compute nao permitir 5 seeds por configuracao, o protocolo precisa ser substituido por um desenho pareado com menos variancia, nao abandonado. O que o refutaria como prioridade e descobrir que a variancia entre seeds e desprezivel neste ambiente — o que so se sabe medindo |
| 6 | Se a taxa de falha geometrica (entidade sem espaco, conexao impossivel) ja for baixa com o roteamento do item 3 e o dimensionamento por razao de receita, a camada hierarquica nao tem o que corrigir e vira burocracia |
| 7 | Se o arquivo QD convergir para elites concentradas em poucas celulas, ou se as celulas preenchidas nao corresponderem a diferencas de desempenho, o descritor de comportamento esta errado — e QD com descritor errado e pior que campeao unico, porque gasta avaliacoes |
| 8 | Se a taxa de propostas sintaticamente validas do modelo local for baixa a ponto de o custo por melhoria aceita exceder o de operadores de mutacao escritos a mao, o LLM nao e o gerador certo nesse orcamento |
| 9 | Se o desempenho nao subir com o aumento do orcamento de simulacoes por decisao, a busca nao esta fazendo trabalho e a familia inteira esta fora de alcance com o custo de simulacao atual |

E o que refutaria o documento como um todo: se as sete medicoes declaradas da auditoria nao se
reproduzirem sob o protocolo do item 5, a secao 5.2 perde a base e as prioridades da secao 6
precisam ser reordenadas a partir das medicoes corretas.

---

## 8. Fontes que nao foi possivel abrir

| Fonte | Motivo | Substituto usado |
|---|---|---|
| ScienceDirect — Deep Blue, *Artificial Intelligence* 134:57-83 | HTTP 403 | Copia espelhada em mimuw.edu.pl, com cabecalho do periodico conferido; DOI via Crossref |
| Science — AlphaZero, 362(6419):1140-1144 | HTTP 403 | Preprint arXiv:1712.01815 (mesmo trabalho); DOI via Crossref |
| OpenReview — PDF da versao NeurIPS 2025 do FLE | HTTP 403 com verificacao de browser | Pagina neurips.cc (de onde vem o numero de 33 tarefas) e o PDF do site do projeto (24 tarefas) |
| ACM Digital Library — PathFinder, FPGA'95 | HTTP 403 | PDF em cecs.uci.edu, texto conferido; DOI via Crossref |
| MIT Press — Novelty Search, *Evolutionary Computation* 19(2) | HTTP 403 | Copia em cs.swarthmore.edu, com linha de procedencia do periodico impressa no PDF; DOI via Crossref |

**Aviso sobre uma armadilha de citacao.** O PDF de "Deep Blue" hospedado em
`pdfs.semanticscholar.org` que aparece no topo das buscas **nao e o artigo**: e um conjunto de
slides de aluno (a primeira pagina traz o nome de um estudante). Qualquer citacao que aponte para
essa URL como sendo o artigo de Campbell, Hoane e Hsu esta errada.
