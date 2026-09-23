# Roadmap: de automacao roteirizada para evolucao por sobrevivencia

Data: 2026-09-23. Este documento lista o que falta implementar para o projeto
satisfazer o requisito do dono: **o agente nao deve ser guiado; deve aprender e
evoluir a partir da persistencia da sobrevivencia**, e deve aprender a ser
engenheiro de producao, nao a se adaptar a um terreno especifico.

O que esta marcado `[medido]` foi verificado nesta maquina, com o comando e a
saida registrados na sessao. O que esta marcado `[proposta]` e desenho, nao
observacao. Nao ha item "pronto" aqui: pronto exige teste que falhava e passa.

---

## 1. O que bloqueia o requisito hoje

### 1.1 Nada persiste entre geracoes `[medido]`

`experiments/curriculum_runner.py:4074` executa `executor.reset(seed=seed)`
antes de cada geracao, e o mundo volta a zero. Varredura do runner:

```
load_entity_state  -> 0 ocorrencias
load_blueprint     -> 0 ocorrencias
lifelong           -> 0 ocorrencias
warm_start         -> 0 ocorrencias
```

Existe um nome reservado para isso, `LIFELONG_CHECKPOINT`
(`experiments/open_play_runner.py:39`), escrito em um unico ponto (`:5584`),
lido por ninguem, e o arquivo `runs/lifelong_champion_state.json` **nunca foi
criado**. A ideia foi cogitada e nao chegou ao caminho que roda.

Consequencia direta: o que se propaga entre geracoes sao 21 numeros do genoma,
nao uma fabrica. Um campeao que sobreviveu recomeca do zero, exatamente como o
primeiro. Selecao por sobrevivencia sem nada que sobreviva nao tem substrato.

### 1.2 O mapa nunca muda, e o gate multi-seed nao testa o que promete `[medido]`

Os patches sao identicos em oito geracoes consecutivas (22 a 29):

```
ferro (27, 83)   cobre (-58.5, 83)   carvao (27, 8.5)
```

A seed incrementa a cada geracao (`iteration_seed = seed + iteration_offset +
index`, 20260931 a 20260938) e **nao altera o terreno**. A causa esta em duas
camadas:

- `fle/cluster/config/map-gen-settings.json` tem `"seed": null`: o mundo e
  gerado uma vez, quando o container do Factorio sobe.
- `integrations/fle.py:176-183` repassa a seed para `environment.reset(...)`,
  que limpa entidades. O terreno ja existe e nao e regenerado.

Isso tem duas consequencias opostas, e as duas importam:

1. **A comparacao campeao-vs-desafiante e limpa**: os dois enfrentam o mesmo
   terreno, as mesmas distancias ate os patches. Nao ha ruido de mundo
   misturado ao de desempenho.
2. **O que a selecao premia e especializacao, nao engenharia.** Um agente
   avaliado sempre no mesmo mapa aprende esse mapa. Nada no fitness distingue
   "sabe construir uma fabrica" de "decorou onde fica o ferro".

O `OpenPlayRobustnessGate` (`learning/robustness.py`, usado em
`open_play_runner.py:5560`) exige 3 seeds distintas, o que parece endereçar
isso. Nao endereça: as seeds nao mudam o terreno, entao as tres passagens
ocorreriam no mesmo mundo. Estado atual do gate, que nunca passou:

```
pass_count: 0 | distinct_pass_seeds: [] | qualified: false
autonomy_runtime_s: 0.0
```

### 1.3 As metricas centrais de sobrevivencia nunca foram populadas `[medido]`

No relatorio da geracao 29, os unicos campos nulos do fitness sao exatamente
os dois que o requisito precisa:

```
fitness com None: ['autonomy_score', 'closed_loop_autonomy']
```

A instrumentacao que os calcularia existe e nao alimenta decisao:
`learning/autonomy.py:155-160` ja deriva `boiler_has_inserter` e
`boiler_has_belt`. `autonomy_soak_runtime_s` e `0.0` em todas as execucoes.

### 1.4 A producao nao vem de rede fisica `[medido]`

Cobertura mineracao-para-processamento de 16,7%; dois `burner-inserter` no
mundo, exatamente os dois que o roteiro coloca; producao constante em
138/92/10 enquanto as chamadas manuais foram de 26 para 191. O transporte e o
personagem, via `insert_item`/`extract_item`.

`total_rate_per_s` soma taxa de lote manual com taxa de fluxo fisico sem
distinguir. Um numero que trata trabalho manual e automacao como a mesma coisa
nao pode selecionar automacao.

---

## 2. Implementacoes, em ordem de dependencia

A ordem importa: mexer no curriculo antes de ter persistencia e medicao
produziria um sistema com aparencia de emergente e sem capacidade de aprender.

### P0 - Sobreviver por tempo suficiente para haver o que selecionar

**P0.1 Alimentacao automatica de combustivel** `[em implementacao]`
Baú + burner-inserter para o boiler e para os drills; liberar os 359 carvoes
do `bootstrap_vault`, que o codigo nunca reabre. Hoje 100% da extracao morre
por falta de carvao (6 drills + 1 boiler com `coal=0`, estavel por ~592 s de
jogo), e as 2 entidades sem energia sao consequencia em cadeia do boiler.
Enquanto isso nao existir, nenhuma fabrica sobrevive tempo suficiente para que
sobrevivencia signifique algo.

**P0.2 Denominador observado nos estagios restantes** `[proposta]`
Feito em cobre (mineracao e fundicao). Faltam `coal`, `baseline`,
`scale_mining`, `smelting_probe`, `belt_smelting`, que ainda dividem pelo
literal do `sleep()` e inflam a taxa em cerca de cinco vezes. Reusar
`planning/fuel.py::observed_window_seconds`.

**P0.3 Sentinela `None` nos tres blocos restantes** `[proposta]`
`curriculum_runner.py:2702`, `:3113`, `:3378` ainda usam
`getattr(ns, k, 0.0)`, que transforma "nao executado" em "medido zero". Esse
padrao ja custou 11 geracoes de diagnostico falso.

### P1 - Medir sobrevivencia

**P1.1 Popular `autonomy_score` e `closed_loop_autonomy`** `[proposta]`
Ligar `learning/autonomy.py` ao fitness. Sem isso o gate de autonomia e
decorativo e o requisito nao tem metrica.

**P1.2 Tempo vivo e mortalidade por causa** `[proposta]`
Registrar, por entidade, quanto tempo esteve produtiva e o que a matou (sem
combustivel / sem energia / sem insumo). Uma fabrica que roda 500 s e morre e
diferente de uma que nasce morta; hoje as duas pontuam igual, porque a medida
e um instantaneo terminal.

**P1.3 Separar producao endogena de intervencao** `[proposta]`
`total_rate_per_s` precisa distinguir o que veio de rede fisica do que veio de
`insert_item`. Enquanto somar os dois, a selecao nao pode preferir automacao.

### P2 - Persistir

**P2.1 Estado que atravessa geracoes** `[proposta]`
Implementar de fato o que `LIFELONG_CHECKPOINT` nomeia: ao fim de uma geracao
promovida, salvar a fabrica (blueprint ou estado de entidades) e iniciar a
geracao seguinte a partir dela, em vez de do zero. E o substrato sem o qual
"persistencia da sobrevivencia" nao existe.

**P2.2 Destruir para reconstruir** `[proposta]`
Com estado persistente, o agente precisa poder demolir parte do que herdou
para refazer melhor. Na literatura isso tem nome: *ruin-and-recreate* / large
neighborhood search (ver `docs/pesquisa-metodologia.md`). Sem isso, herdar
estado vira divida tecnica acumulada em vez de vantagem.

### P3 - Generalizar, em vez de decorar o mapa

**P3.1 Variacao real de terreno** `[proposta]`
Fazer a seed chegar ao gerador de mapa (`map-gen-settings.json` hoje com
`"seed": null`), o que exige recriar o mundo e nao apenas limpar entidades.
E a mudanca mais invasiva da lista, porque altera o ciclo de vida do container.

**P3.2 Avaliacao em conjunto fixo de mapas** `[proposta]`
A tensao entre "mapa fixo da comparacao limpa" e "mapa variavel forca
generalizacao" nao se resolve escolhendo um lado. Resolve-se avaliando cada
genoma sobre o **mesmo conjunto** de N mapas e agregando (media, ou pior caso
para pressionar robustez). A comparacao continua justa, porque todos enfrentam
o mesmo conjunto, e o fitness deixa de premiar quem decorou um terreno. E a
pratica padrao em avaliacao de generalizacao em RL.

**P3.3 Corrigir o gate multi-seed** `[proposta]`
Depois de P3.1, o `OpenPlayRobustnessGate` passa a testar o que promete. Antes
dela, exigir "3 seeds distintas" da uma garantia que o mecanismo nao entrega.

### P4 - Trocar roteiro por pressao seletiva

**P4.1 Curriculo como objetivo, nao como sequencia de acoes** `[proposta]`
Os 16 estagios executam acoes predeterminadas. Para o comportamento emergir,
o estagio precisa declarar **o que** deve ser verdade (ha placas de ferro
sendo produzidas de forma sustentada) e nao **como** consegui-lo. Ultimo item
da lista de proposito: sem P0-P3, trocar o roteiro produz um sistema que
parece emergente e continua sem poder aprender.

---

## 3. Dividas de instrumentacao que enganam quem le

Nao bloqueiam a evolucao, mas produzem confianca indevida e devem ser
corrigidas antes de qualquer afirmacao publica de desempenho.

| Item | Estado | Evidencia |
|---|---|---|
| `risk_accuracy: 0.9928` sobre classe com taxa base de 92,93% | pior que o baseline trivial, que esta no mesmo arquivo (`risk_persistence_accuracy: 1.0`) e nenhum gate consulta | `[medido]` |
| World model retreinado a cada geracao e nunca carregado | zero call-sites de predicao; perde da persistencia em 4/4 folds | `[medido]` |
| `spatial_policy.npz` carregado, metadata descreve `spatial_policy_mlp.npz` | md5 diferentes | `[medido]` |
| `not_connected` em `factory_graph.py:356` e `autonomy.py:13` | status inexistente em `defines.entity_status` do Factorio 2.0.73 | `[medido]` |
| (1+1)-ES mutando 21 parametros com 19 avaliacoes | menos de uma avaliacao por dimensao | `[medido]` |

---

## 4. Dominios do jogo ausentes por inteiro `[medido]`

Poluicao e biters, rede eletrica como modelo (so leitura de status textual),
trens, throughput de esteira por tier, e depleção de patch. Isso delimita o
escopo honesto do que existe: um agente de bootstrap early-game sem pressao
externa, nao um construtor de fabrica.

Enquanto nao houver pressao externa (biters, poluicao, escassez), "sobreviver"
significa apenas "nao ficar sem carvao". A pressao seletiva do requisito e
mais fraca do que parece.
