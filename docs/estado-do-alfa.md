# Estado do alfa: o que foi corrigido e o que resta

Data: 2026-09-23. Este documento nao e plano de fases. E o registro do que foi
corrigido, com a evidencia que sustenta cada item, e a lista fechada do que
falta para o alfa - aquilo que, uma vez feito, permite deixar o sistema
evoluindo sem que ele acumule ruido com aparencia de conhecimento.

Criterio de "fechado": teste que falhava e passa, suite verde, exit code
registrado. Nada aqui e marcado fechado por inspecao visual.

---

## O achado que organiza todo o resto

Quatro dos cinco bloqueios encontrados nao estavam na inteligencia do agente.
Estavam na **instrumentacao**, e de um jeito que produzia diagnostico confiante
e falso:

| defeito | efeito | evidencia |
|---|---|---|
| `info["error"]` lido de uma chave que o FLE nao produz | toda mensagem de falha descartada; contraexemplos com `"error": null` | `fle/env/gym_env/environment.py:504` entrega `result`, nao `error` |
| `getattr(ns, k, 0.0)` como default | "nao executado" virava "medido zero", e o zero virava o sintoma | `circuit_iron_ore: 0.0` enquanto o bau tinha 137 minerios, medido por RCON |
| FLE marca falha por substring "error" no texto impresso | uma variavel chamada `circuit_nav_error` reprovou um estagio que produzira 5 circuitos | `environment.py:451` |
| denominador = literal do `sleep()` | toda taxa inflada ~5x; o campeao registra copper-ore a 0,8125/s contra 0,25/s de capacidade fisica do drill | janela real medida ~80 s contra literal de 16 |
| metrica media dose de combustivel, nao producao | `ore == floor(6,667 x carvao)`; o gate de cobre era o teste `coal_budget >= 2` | 9 execucoes com 1 carvao deram exatamente 6; 7 com 2 deram exatamente 13 |

Enquanto um instrumento converte "nao executado" em "medido zero", geracoes nao
acumulam conhecimento: acumulam ruido com aparencia de dado. Por isso a ordem de
trabalho foi instrumentacao antes de mecanismo.

---

## Fechado

Cada item abaixo tem commit, teste que falhava e passa, e suite verde.

**Payload ao vivo em JSON valido** (`30e87d4`). O WebSocket serializava custo de
rota infinito como o token bare `Infinity`, que `JSON.parse` rejeita, e o
navegador descartava 100% das atualizacoes. Medido antes: 27 erros de console,
zero payload aceito. Depois: 3/3 parseados.

**Regua comensuravel na selecao** (`ddf7d37`). O fitness do campeao tinha 7
chaves; o do desafiante, 14. Tres restricoes duras reprovavam por metricas que o
incumbente nunca enfrentou - o incumbente nao era dificil de bater, era
imbativel. Replay sobre os 23 desafiantes historicos: zero vereditos mudam, o
que confirma que a correcao remove o veto ilegitimo sem promover ninguem
retroativamente.

**Footprint real no planejamento de rota** (`d099e11`). O A* usava raio fixo
adivinhado. Medido na cena real: 75 tiles ocupados ficavam livres e 21 livres
eram bloqueados; `electric-mining-drill` ocupa 45 tiles e marcava 5. A rota
atravessava o interior de uma assembling machine e a falha so aparecia depois,
no `place_entity`.

**Erro real do estagio registrado, e nao-medido separado de zero** (`c68e698`).
Ver tabela acima. Foi o que permitiu, na geracao seguinte, ler a mensagem que
faltava ha 11 geracoes.

**Bau de ferro recuperado** (`0ad948e`). A variavel `chest` vinha `None` do
namespace do FLE; a entidade existia no mundo o tempo todo. Recuperacao por
varredura, sem posicao fixa no codigo.

**Heuristica de falha do FLE nao disparada por nome de variavel** (`8619fb9`).
E `tests/test_fle_triggers.py` fecha a armadilha para os 17 scripts do
curriculo.

**Producao de cobre medida por janela e carga reais** (`d4d1f8f`). Inclui
`planning/fuel.py`, que dimensiona carga a partir das figuras do runtime
(carvao 4 MJ, drill 150 kW, fornalha 90 kW), e `measurement_protocol` no
`FitnessVector`: taxas medidas por instrumentos diferentes sao declaradas
incomensuraveis em vez de comparadas. Sem isso, corrigir o denominador tornaria
o piso do campeao inatingivel e reprovaria 9 metricas de uma vez.

**Alimentacao automatica das queimadoras** (`4192d17`). Bau + `BurnerInserter`
por maquina, carga dimensionada pelo horizonte de geracao. Inclui
`purpose="infrastructure"` no executor: instalar automacao deixou de ser contado
como logistica manual, o que faria `survival.py` registrar a instalacao da
automacao como regressao.

**Mapa da fabrica reescrito** (`d3f3e48`, `19bd5a7`, `f42f069`, `c323b4b`,
`db81107`). Canvas no cliente com camera local, sprites assentados pelo
footprint real do prototype, texturas oficiais do jogo, navegacao por toque, e
versionamento de asset por hash de conteudo - o bundle foi reconstruido seis
vezes enquanto as paginas pediam `?v=0.12.0`, e um celular que tivesse aberto o
painel antes servia do cache a versao que congelava o canvas.

---

## Em execucao

Quatro frentes, em arquivos disjuntos, com o mesmo criterio de fechamento.

1. **Janela observada e sentinela nos estagios restantes.** Nove estagios ainda
   dividem pelo literal do `sleep()`; tres blocos ainda usam `getattr(..., 0.0)`.
2. **Metricas de sobrevivencia.** `autonomy_score` e `closed_loop_autonomy` sao
   os unicos campos nulos do fitness da geracao 29, e sao exatamente os que o
   requisito precisa. Inclui separar producao endogena de intervencao, e medir
   tempo vivo e causa da morte em vez de instantaneo terminal.
3. **Persistencia entre geracoes.** `LIFELONG_CHECKPOINT` e um nome reservado
   sem implementacao: escrito em um ponto, lido por ninguem, arquivo nunca
   criado. Sem substrato que sobreviva, selecao por sobrevivencia nao tem sobre
   o que operar.
4. **Generalizacao.** Os patches sao identicos em oito geracoes (ferro 27,83;
   cobre -58.5,83; carvao 27,8.5) enquanto a seed incrementa - `"seed": null` no
   map-gen e `environment.reset()` que limpa entidades sem regenerar terreno.
   O gate multi-seed rodaria as tres seeds no mesmo mundo.

---

## Limite de escopo que nao e defeito

Quatro dominios do jogo estao ausentes por inteiro: poluicao e biters, rede
eletrica como modelo, trens, e throughput de esteira por tier. Enquanto nao
houver pressao externa, "sobreviver" significa apenas "nao ficar sem carvao", e
a pressao seletiva e mais fraca do que o nome sugere. Isso delimita o que o alfa
pode demonstrar: um agente de bootstrap early-game que se sustenta, nao um
construtor de fabrica sob ameaca.
