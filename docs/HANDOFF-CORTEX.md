# Handoff: do executor de roteiro ao córtex guiado por LLM

Data: 2026-09-23. Branch `fix/live-payload-e-mapa`, sincronizada com `origin`.
Este documento entrega o estado do sistema, o diagnóstico de por que ele **não**
aprende, e a arquitetura-alvo. Escrito para quem vai continuar, não para quem
escreveu.

Regra que vale para todo o documento: número sem medida não entra. Onde há
número, há comando que o produziu. Onde não há, está marcado.

---

## 1. O diagnóstico, em uma frase

O sistema tem estado, objetivo e memória. **Não tem política.** A política é
código escrito à mão: 17.713 linhas de runner decidem o que fazer, em que
ordem, onde colocar e como conectar. O agente não escolhe — executa.

O que é chamado de "evolução" são 21 parâmetros escalares mutados em torno de
um roteiro fixo:

```
routing_turn_penalty, placement_exploration, placement_radius_scale,
coal_safety_stock, coal_producer_refuel, buffer_target, autonomy_belt_margin,
autonomy_pole_margin, autonomy_route_detour_margin, ...
```

São coeficientes, não decisões. A distinção que importa, e que o dono do
projeto formulou melhor do que eu:

> Uma coisa é ele ter de baseline estratégias, conhecimento de engenharia de
> produção e otimização matemática e algorítmica (como A*). O que parece é que
> está sendo feito "faça isso desse jeito, pegue a esteira e conecte dessa forma
> na fornalha que você construiu ali". Algo muito mecânico.

Ele está certo. Dar A*, catálogo de receitas e modelo de custo é baseline
legítimo. Dar a sequência de ações é automação.

---

## 2. Onde o sistema está, medido

### 2.1 Laço em ponto fixo

```
campeão        geração 37, promovida 04:02Z
checkpoint     curriculum-20260923T035414Z
última         geração 82
promoções      nenhuma desde a 37
```

Todas as gerações restauram o mesmo mundo do checkpoint, constroem
aproximadamente a mesma coisa, chegam a 14 de 16 estágios e são rejeitadas.
Visualmente o mapa se repete porque **é** o mesmo mapa.

### 2.2 O campeão foi medido com instrumento quebrado

`learning/factory_graph.py` calculava o pickup do inserter como
`posição - direção` e o drop como `posição + direção`. Está invertido. Sondado
contra a resposta da engine, **18 de 18 inserters discordavam**:

```
@(25.5,7.5) dir=0  engine: pega do baú @(25.5,6.5), larga na broca @(25.5,8.7)
                   grafo:  pega da broca
```

Consequência medida: `chain_feeds` reportava 0 num mundo com sete
alimentadores; os 20 baús liam como "alimentados, alimentando nada".

Isso alimenta `producers_reaching_processor`, `isolated_producers` e
`physical_processing_coverage` — **entradas de aptidão**. Corrigido em
`700460c`, mas o campeão da geração 37 foi medido antes. **Ele não é uma
baseline válida**, e é por isso que o reset do checkpoint não é só conveniência:
é correção.

### 2.3 A fábrica não é uma fábrica

Leitura do mundo por RCON, com o recurso sob cada entidade:

```
fornalhas (5)   3 sem ingrediente · 1 trabalhando · 1 com saída cheia
brocas (9)      5 sem combustível · 3 trabalhando · 1 com baú cheio
braços (10)     7 esperando material · 2 com destino cheio · 1 trabalhando
braços sobre tile de minério: 20 de 22
```

São máquinas isoladas, cada uma esperando o que não chega. A cadeia
carvão→queimador foi fechada parcialmente em `700460c` (2 de 15 alvos, 16
esteiras, baús com 138 e 121 de carvão contra os 16–33 que o alimentador
carrega). Minério→fornalha e saída da fornalha continuam abertas.

---

## 3. Inventário: o que existe e o que está desligado

### 3.1 Peças que seriam a política, e não são consultadas

| módulo | linhas | estado |
|---|---|---|
| `learning/spatial_policy.py` | 332 | treinada, **nunca consultada em produção** |
| `learning/torch_spatial_policy.py` | 294 | idem |
| `planning/dependency_plan.py` | 1125 | **zero chamadores** fora do painel |
| `learning/repair_loop.py` | 1418 | ligado ao runner em `658dae5`; escolhe por regra fixa |
| `learning/archive.py` | 587 | nichos MAP-Elites; 32 de 37 gerações recusadas por falta de proveniência |
| `planning/patterns.py` | 1302 | blocos proporcionais derivados do runtime, **sem chamador** |
| `learning/knowledge.py` | 322 | 500+ lições; entram no contexto do advisor desde `069f049` |

### 3.2 Peças que são ferramenta legítima (mantenha)

| módulo | o que entrega |
|---|---|
| `planning/astar.py` | rota ponto a ponto com peso de curva |
| `planning/runtime_catalog.py` | 217 receitas lidas do jogo, com status por campo |
| `planning/footprints.py` | footprint real por RCON, com fallback |
| `planning/placement.py` | decide por tiles; precifica tile com minério; broca é exceção |
| `planning/delivery.py` | liga fonte→destino: braço, ou braço+esteira+braço com A* |
| `planning/resupply.py` | saca de contêiner sem recolher; recupera baú órfão pelo grafo |
| `planning/rebuild.py` | ruin-and-recreate com modelo de custo e recusa nomeada |
| `learning/survival_analysis.py` | Kaplan-Meier com censura, hazard, riscos competitivos, portão de amostra |
| `learning/factory_graph.py` | grafo material do mundo, com classificadores de parada |

Essas são o "baseline de engenharia" legítimo. O problema nunca foi elas
existirem — é elas serem **chamadas por um roteiro** em vez de **escolhidas
por uma política**.

### 3.3 O peso morto

```
experiments/curriculum_runner.py   10.615 linhas
experiments/open_play_runner.py     5.746
experiments/evolution_loop.py       1.352
```

São 16 estágios escritos à mão, cada um com posição, ordem e conexão fixas.
Esse é o código que precisa deixar de ser o caminho principal.

---

## 4. Arquitetura-alvo: córtex guiado por LLM

### 4.1 O que falta é o espaço de ação

Hoje o agente não pode "colocar X em Y" nem "conectar A a B" como decisão sua.
Só pode executar o script da etapa 7. Sem espaço de ação explícito, não existe
política, e sem política não existe aprendizado — só automação.

O espaço de ação mínimo, derivável do que já existe:

```
PLACE(prototype, anchor, constraints)      -> planning/placement.py resolve o tile
CONNECT(source, target, item, budget)      -> planning/delivery.py resolve a ligação
FEED(machine, item, amount)                -> planning/resupply.py resolve a fonte
DEMOLISH(entities, reason)                 -> planning/rebuild.py precifica e recusa
RESEARCH(technology)                       -> set_research, com dependency_plan validando
CRAFT(item, amount)                        -> dependency_plan expande a cadeia
OBSERVE(region)                            -> factory_graph mede
```

Cada primitiva **já tem implementação**. O que não existe é o agente poder
escolher entre elas.

### 4.2 O laço do córtex

```
  observar        factory_graph + world scene + inventário
      |
  situar          dependency_plan: dado o objetivo, o que falta e em que ordem
      |
  deliberar       LLM propõe N ações candidatas do espaço, com justificativa
      |
  filtrar         viabilidade dura: tecnologia, material, geometria, custo
      |           (as ferramentas recusam com motivo — isso não é opinião)
      |
  escolher        política: bandit sobre histórico, ou valor aprendido
      |
  agir            executa a primitiva
      |
  medir           a previsão se cumpriu? (repair_loop já faz isto)
      |
  registrar       sintoma -> ação -> previsão -> resultado no ledger
      |
  (retroalimenta a escolha da próxima vez)
```

`learning/repair_loop.py` **já implementa este laço em miniatura**, para
reparos. Leia-o antes de projetar: `detect_deficits`, `diagnose`,
`propose_actions`, `order_steps`, `evaluate_prediction`, `record_repair`. A
generalização é trocar "déficit" por "objetivo" e ampliar o espaço de ação.

Prova de que funciona: rodado sobre a geração 57 sem nenhuma dica, o laço
propôs sozinho `attach_machine_to_live_network` para o assembler sem energia —
a mesma correção que levou horas de diagnóstico humano — e pontuou `held`
(saída 0.0 → 7.0, recompensa 1.0) contra o que de fato aconteceu.

### 4.3 O papel da LLM

A LLM **não** deve executar ações nem escrever o script. Deve:

1. **Propor candidatas** a partir do estado e do objetivo, em linguagem do
   espaço de ação, com justificativa explícita.
2. **Explicar a falha** quando uma previsão não se cumpre, gerando a hipótese
   que vira a próxima tentativa.
3. **Consolidar memória**: transformar o histórico de sintoma→ação→resultado em
   regras candidatas, que são testadas, não aceitas.

O que ela **não** deve fazer: decidir sozinha. A escolha final sai do filtro
duro (viabilidade) mais a política (histórico). Uma proposta da LLM que não
passa no filtro é descartada com motivo registrado — isso é o que impede
alucinação de virar ação.

Infra já existente: `agents/evolution_advisor.py` (291 linhas) fala com
Qwen3-4B local na porta 18081, com orçamento de contexto por caracteres e
degradação por recência. O orçamento foi corrigido em `45508b5`: cortava por
constante fixa e descartava evidência que cabia.

### 4.4 Memória

Três camadas, todas já com substrato:

- **Episódica**: `runs/repairs.jsonl` (27 linhas) — sintoma, ação, previsão,
  veredito. É o que permite a escolha deixar de ser regra fixa.
- **Semântica**: `runs/knowledge.jsonl` (500+ lições, 163 verificadas) — o
  verificador rejeita número sem suporte nos fatos. Ver `learning/knowledge.py`.
- **Estrutural**: `runs/niche_archive.json` — MAP-Elites, um elite por nicho
  comportamental (fração endógena, capacidades construídas, causa de parada).

O que falta: nada disso realimenta a **escolha de ação**. `select_action` em
`repair_loop.py` já aceita uma função de score compatível com
`UCB1Bandit.stats()`; o ledger existe; a costura está pronta e sem uso real.

---

## 5. Medição: o que já foi consertado e por quê importa

Este bloco não é histórico — é a razão de a política poder aprender de sinal em
vez de ruído. Cada item abaixo era um instrumento que mentia.

| defeito | efeito medido | commit |
|---|---|---|
| grafo lia inserters invertidos | `chain_feeds` 0 com 7 alimentadores; 18/18 discordam da engine | `700460c` |
| régua punia quem avança | 12 de 37 gerações rejeitadas só por tentar o estágio seguinte | `8223cc4` |
| capacidade herdada creditada ao genoma | herdeiro que nada construiu ganhava 2 capacidades | `80762b6` |
| produção do ancestral contada como do desafiante | 7 placas próprias contra 83 do contador global (91% do ancestral) | `69d5a07` |
| trava absoluta que o próprio campeão não cumpre | cobertura ≥50% exigida de quem tinha 18,2%, com campeão em 16,7% | `36e947b` |
| horizonte de combustível 6× o consumo real | `coal_needed_total` 1989 contra 340 medidos | `4410b6e` |
| receita sem tempo virava 0,5 s inventado | toda contagem de máquina dimensionada sobre número inexistente | `ad162a5` |
| `connect_entities` devolve sucesso sem energizar | assembler a 0 J com rede viva; alcance de fio 9 vs área de suprimento 3,5+1,5 | `2f5cc61` |
| catálogo do planejador com 11 receitas de 217 | agente não sabia que aço existe nem que máquina é feita de circuitos | `33c4c76` |

**Cinco defeitos distintos da mesma família**: ausência convertida em medida.
`getattr(ns, k, 0.0)`, `row.get("energy", 0.5)`, `metrics.get(k, 0.0)`. Esse
padrão custou 11 gerações de diagnóstico falso e é a primeira coisa a procurar
em qualquer código novo aqui.

---

## 6. Armadilhas do ambiente (leia antes de escrever código)

1. **O FLE marca falha por substring "error" no texto impresso**
   (`fle/env/gym_env/environment.py:451`). Uma variável chamada
   `circuit_nav_error` reprovou um estágio que tinha produzido 5 circuitos.
   Guardas: `tests/test_fle_triggers.py`, `tests/test_fle_scripts.py`,
   `tests/test_stage_namespace_contract.py`.

2. **`connect_entities` devolve sucesso deixando o alvo sem energia.** Alcance
   de fio 9, área de suprimento 3,5 + meia-extensão da máquina. Leia o
   resultado de volta. **Cinco pontos de chamada ainda não leem**, três deles no
   estágio 14 (`curriculum_runner.py` ~5013, ~5054, ~5102, ~7323, ~7468).

3. **`place_entity_next_to` snapa entidade de lado par na fronteira do tile.**
   Derive rotação e tile de entrega da posição **devolvida**, nunca do lado
   pedido. Isso causou carvão caindo no chão com três testes AST afirmando o
   contrato errado e passando.

4. **`place_entity_next_to` constrói sem ter o item** (`server.lua:533-555`
   cria a entidade e só então tenta remover do inventário, sob `can_insert`, que
   testa se cabe e não se existe). Não construa nada que dependa disso.

5. **O mundo é restaurado do checkpoint a cada geração.** Só quem é promovido
   grava. `learning/lifelong.py` explica por que `load_blueprint` foi rejeitado:
   seu `server.lua:7` chama `research_all_technologies()`.

6. **`/tmp` é partição de 437 MB e enche.** Com ela cheia a suíte dá
   `20 failed, 144 errors` por falha de escrita — falso negativo perfeito.
   Verifique `df -h /tmp` antes de acreditar numa suíte vermelha.

7. **`.pyc` invalida por (mtime, size).** Uma mutação que preserva o tamanho
   dentro do mesmo segundo faz o Python servir bytecode obsoleto. Isso
   contaminou uma validação por mutação nesta sessão. Limpe `__pycache__` entre
   execuções e prefira mutar em cópia fora do caminho vivo.

8. **`runs/game_knowledge_graph.json` é arquivo vivo.** Mudou de 24 para 34
   receitas habilitadas e de 1 para 3 tecnologias pesquisadas numa mesma sessão.
   Teste que fixe `enabled`/`researched` literal vai piscar.

9. **Só 3 de 196 tecnologias estão pesquisadas.** `splitter` e `underground-belt`
   exigem `logistics`: **0 de 57** tentativas de open play chegaram lá. Qualquer
   padrão que dependa de splitter não é construível hoje.

10. **O cenário é um laboratório de concreto**, não mundo natural:
    `refined-concrete` 176 runs, `water` 49, `natural: 0` — zero árvores, zero
    rochas. Não há madeira; baú de madeira não é fabricável, só `iron-chest`.

---

## 7. Ordem sugerida

**Fase 0 — reset da baseline.** Apagar `runs/evolution_champion.json` e
`runs/lifelong_champion_state.json*`. Justificativa acima: o campeão foi medido
com o grafo invertido. Sem isso, toda comparação carrega um piso inválido.

**Fase 1 — espaço de ação.** Tornar as sete primitivas da §4.1 chamáveis com
parâmetros, cada uma devolvendo sucesso medido ou recusa nomeada. Nenhuma
lógica nova: é fachada sobre o que existe. Critério: um teste que executa uma
sequência de primitivas e constrói uma cadeia funcional sem passar por
`curriculum_runner`.

**Fase 2 — laço de deliberação.** Generalizar `repair_loop` de déficit para
objetivo. O ledger passa a registrar toda ação, não só reparo. Critério: o laço
propõe e executa uma cadeia que um estágio escrito à mão faria, e a previsão se
cumpre.

**Fase 3 — LLM propondo.** O advisor deixa de sugerir ajuste de parâmetro e
passa a propor ações do espaço, com justificativa. Filtro duro antes da
execução. Critério: proporção de propostas viáveis, e diferença medida contra a
política de regra fixa.

**Fase 4 — política aprendida.** `select_action` passa a consultar o ledger via
bandit; depois, valor aprendido. Critério: taxa de previsão cumprida subindo ao
longo das gerações, com o gate de amostra de `survival_analysis.py` dizendo
quando há evidência suficiente.

**Fase 5 — currículo derivado.** As 16 etapas viram objetivos, não receita.
`dependency_plan` deriva a ordem. `curriculum_runner` vira baseline de
comparação, não caminho principal.

Expectativa honesta: nas fases 2 a 4 o agente vai jogar **pior** que o roteiro
afinado à mão, por muitas gerações. Isso é o custo de trocar execução por
exploração, e precisa estar combinado antes de começar, senão a primeira queda
de métrica parece regressão.

---

## 8. Como saber se funcionou

Não use "estágios completados" como critério da nova arquitetura — é a métrica
do roteiro. Use:

- **Proporção de ações escolhidas cuja previsão se cumpriu**, por geração
  (`runs/repairs.jsonl` já tem o formato).
- **Diversidade de nichos ocupados** no arquivo MAP-Elites. Hoje: 1 nicho, 32
  de 37 gerações recusadas por proveniência ausente.
- **Descobertas retidas contra descartadas** (`learning/discovery.py`). Hoje: 32
  achados, 0 retidos — o sistema encontrava e jogava fora.
- **Sobrevivência com censura** (`learning/survival_analysis.py`), com o portão
  de amostra publicado junto. Hoje o portão diz `insufficient` com 6 eventos e
  exige 10.

---

## 9. Estado operacional

```
host          100.112.155.114 (Kali), usuário ti, chave ~/.ssh/kali_box_ed25519
repositório   /srv/factorio-ai-lab
branch        fix/live-payload-e-mapa, sincronizada com origin
serviços      factorio-ai-evolution (Restart=always, 1 geração por execução)
              factorio-ai-dashboard (porta 8765)
              factorio-ai-llm (Qwen3-4B, porta 18081)
container     fle-local-factorio_0-1 — NÃO sobe sozinho após reboot
suíte         1220 passed, exit 0; ruff limpo
```

Três coisas operacionais que valem correção:

1. O container do Factorio não tem `restart` configurado: um reboot derruba o
   laço até alguém perceber.
2. `factorio-ai-llm` vazou 7 GB em 13 horas (RssAnon 10,9 GB para um modelo de
   2,4 GB), saturou a máquina e derrubou o SSH. Não há `MemoryMax` na unidade.
3. Não existe passo de implantação: editar o arquivo em `/srv` já é implantar,
   e o laço relê o disco a cada geração. A geração 37 foi promovida com código
   não commitado por causa disso. `code_revision` no relatório de geração
   (commit `711961c`) registra commit e árvore suja — use isso para atribuir
   resultado a código.

---

## 10. O que eu faria diferente

Registro por honestidade, não por autocrítica: passei o dia consertando
instrumento, medição e régua. Cada correção era real e provada, e nenhuma delas
faz uma esteira existir. Quando o laço travava, eu diagnosticava e escrevia o
conserto daquele fluxo — seis vezes seguidas. Isso não escala e, pior, **é a
própria coisa que o projeto deveria estar aprendendo a fazer sozinho**.

O trabalho de medição não foi desperdício: sem ele a política aprenderia de
ruído, e cinco dos instrumentos mentiam. Mas a direção estava errada desde o
momento em que a primeira cadeia foi escrita à mão em vez de proposta pelo
agente.
