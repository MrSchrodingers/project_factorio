# Estado do alfa: o que foi corrigido e o que resta

Data: 2026-09-23. Este documento nao e plano de fases. E o registro do que foi
corrigido, com a evidencia que sustenta cada item, e a lista fechada do que
falta.

Criterio de "fechado": teste que falhava e passa, suite verde, exit code
registrado. Nada aqui e marcado fechado por inspecao visual.

---

## O achado que organiza esta rodada

A regua de selecao punia quem avancava.

`compare_challenger` comparava `challenger.failures > champion.failures` como
contagem absoluta. O incumbente era a geracao 6, promovida quando o curriculo
parava antes; ela registra `failures=0` porque nunca enfrentou o estagio
dificil. Todo desafiante posterior roda 16 estagios, completa 13 ou 14 - mais
longe do que o incumbente jamais foi - falha o seguinte, e le como `0→1`.

Doze das 37 geracoes gravadas foram rejeitadas com essa como unica regressao:
7, 10, 11, 15, 29, 30, 31, 32, 33, 34, 35 e 36. Tres delas - 29, 34 e 36 -
haviam completado `Electronic circuits`, o estagio em que a corrida estava
parada desde a geracao 13.

Precisao que a primeira versao deste documento nao tinha: o estagio nao e
confiavelmente alcancavel. Foi completado em 4 das 26 geracoes que gravaram
nomes de estagio (29, 34, 36 e 37), isto e, 15% delas. O numero acima descreve
o que a selecao descartou, nao um gargalo resolvido.

O efeito composto, medido pelo registro de descobertas sobre o historico real:

| tipo de achado | total | retidos | descartados |
|---|---:|---:|---:|
| melhoria de rota | 12 | 0 | 12 |
| capacidade vista pela primeira vez | 11 | 0 | 11 |
| deslocamento de gargalo | 9 | 0 | 9 |

A rota mais barata que a busca ja produziu, 7.252 contra 7.505 do incumbente,
foi encontrada na geracao 21 e descartada.

Uma causa, tres sintomas. Sem promocao, o arquivo de nichos nao recebe
linhagem nova, a heranca entre geracoes nao captura nada (a captura exige
promocao) e nenhuma descoberta e retida. Nao era falta de capacidade do
agente: era a regua.

A correcao compara estagios por nome. Falhar um estagio que o incumbente
completou e capacidade perdida; falhar um que ele nunca completou e custo de
explorar e nao decide. Quando os nomes nao existem dos dois lados, o eixo e
declarado incomensuravel em vez de decidido por contagem - a mesma disciplina
que `_commensurate` ja aplicava as metricas opcionais.

**Verificado em producao.** A geracao 37 rodou com a correcao em disco e foi
promovida: primeira promocao desde a geracao 6. Seu fitness registra 14
estagios completados, `failed_stages: ['Logistic science']`, `failures: 1` -
um campeao que a regua antiga teria considerado impossivel - mais
`halt_cause: fuel_and_power_starvation` e `productive_runtime_s: 680.4`.

---

## Fechado nesta rodada

**Ruin-and-recreate como operacao precificada** (`aede3bf`). Tres operadores de
destruicao sobre o grafo medido, modelo de custo que compara tempo de parada e
material contra a capacidade projetada, e recusa nomeada quando o ganho nao
cobre o custo. A capacidade projetada e medida pelo mesmo `build_factory_graph`
que mediu a linha de base. Terminacao argumentada: todo round aceito decresce
estritamente o par (produtores desconectados, contagem de esteiras).

**Analise de sobrevivencia com censura** (`e6710b1`). Kaplan-Meier com variancia
de Greenwood e limites log-log, hazard condicional com Nelson-Aalen, incidencia
cumulativa de Aalen-Johansen para causas competidoras. Cox, log-rank e p-valor
nao foram implementados: com este n transfeririam confianca que o dado nao
carrega. Todo resultado carrega um portao de amostra que hoje responde
`insufficient` e diz quantos eventos faltam.

**Arquivo de nichos** (`08c968e`). Um elite por nicho comportamental, seguindo
Mouret e Clune, "Illuminating search spaces by mapping elites"
(arXiv:1504.04909, 2015). Tres eixos ja medidos: fracao endogena da producao,
capacidades construidas por este genoma e causa de parada. Nichar por modo de
falha guarda a melhor solucao de cada forma de morrer. A ocupacao e decidida
por `compare_challenger`, nao por escalar inventado.

**Registro de descobertas** (`11d9bef`). Recupera do historico o que cada
geracao encontrou e se foi retido. Retencao tem tres estados: retido,
descartado e sem veredito gravado.

**Portao do world model em tempo de chamada** (`c2da924`). Toda chamada declara
qual modelo respondeu, contra qual linha de base, em qual feature, e se aquele
par foi medido. Feature nao coberta pelo holdout e recusada em vez de
respondida pelo veredito agregado.

**Sobrevivencia exposta no painel** (`df71c35`). `GET /api/evolution/survival`,
com o portao de amostra viajando junto do numero e cache invalidado por
fingerprint do diretorio, nao por tempo.

---

## O que resta

1. **Ligar os modulos ao laco.** `rebuild.py`, `archive.py` e
   `world_model_policy.py` estao commitados e nao tem chamador. Enquanto nao
   tiverem, sao biblioteca, nao comportamento.
2. **Memoria de conhecimento na decisao.** 370 licoes gravadas, 163 verificadas.
   O unico uso hoje e contar linhas (`knowledge_count_at_selection`). Nenhuma
   licao e lida para decidir nada.
3. **Frontend.** Nenhum dos endpoints novos e renderizado. A evolucao temporal
   esta observavel por `curl`, nao no painel.
4. **Responsividade mobile**, deixada para o fim por pedido do dono.

---

## Limite de escopo que nao e defeito

Quatro dominios do jogo estao ausentes por inteiro: poluicao e biters, rede
eletrica como modelo, trens, e throughput de esteira por tier. Enquanto nao
houver pressao externa, "sobreviver" significa apenas "nao ficar sem carvao", e
a pressao seletiva e mais fraca do que o nome sugere. Isso delimita o que o
alfa pode demonstrar: um agente de bootstrap early-game que se sustenta, nao um
construtor de fabrica sob ameaca.

Duas limitacoes do eixo de tempo estao registradas no codigo e valem repetir
aqui. `productive_runtime_s` e um limite inferior somado de janelas de estagio
cronometradas, entao o tempo e quantizado pelo desenho do curriculo.
`halt_cause` e um veredito sobre o snapshot terminal e nao data a falha que
reporta. Datar exige uma serie temporal de status que a instrumentacao ainda
nao emite.
