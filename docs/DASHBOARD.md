# Dashboard e control plane

O dashboard é a camada read-only de observabilidade do laboratório. O processo web não
executa ações de gameplay e não chama reset do ambiente. Quem modifica o mundo é o runner de
experimentos/currículo, separado do servidor web.

## Endpoints

- / — frontend HTML/CSS/JS sem CDN.
- /api/status — saúde de Factorio, LLM, memória, renderer e configuração ativa.
- /api/world — snapshot atual de entidades e contadores acumulados de produção.
- /api/world/frame.png — frame raster 1024x1024 do mundo observado.
- /api/assets/icon/{prototype}.png — ícone oficial disponível apenas no runtime local.
- /api/production?precision=1m — 300 amostras nativas de LuaFlowStatistics.
- /api/history — telemetria coletada durante conexões WebSocket.
- /api/learning — estado do experimento UCB1 offline.
- /api/run — execução/currículo ativo.
- /api/research — estágios, trials online, métricas e próxima ação.
- /api/knowledge — lições estruturadas retidas pelo laboratório.
- /api/datasets — contagem e gate de treinamento das demonstrações espaciais.
- /api/experiments/routing — agregação do sweep A*.
- /api/config — parâmetros runtime editáveis.
- /ws/live — mundo, research state, run, conhecimento e telemetria ao vivo.

## Live Factory

O painel principal não é mais uma visualização de quadrados abstratos. Ele gera um frame
raster do mundo observado com:

- textura de terreno do Factorio;
- água e patches de recursos coletados do mundo real;
- árvores/rochas quando disponíveis no snapshot;
- ícones oficiais para drills, chests, furnaces, belts e demais prototypes;
- posição/direção das entidades;
- zoom, pan e reset de viewport no navegador;
- atualização de frame desacoplada da telemetria estruturada.

O terreno e os sprites de máquinas suportados são lidos da distribuição Linux demo do
Factorio 2.0.73 instalada localmente. Os sheets de recursos como iron ore, copper ore, coal
e stone são lidos do cache local de sprites do FLE. Nenhum desses binários é versionado ou
redistribuído pelo repositório; o Git contém apenas o código que resolve e renderiza os
arquivos locais.

## Princípios visuais do v0.5

O control plane não tenta reproduzir a tela do jogo pixel a pixel. Ele usa os assets do
runtime para construir uma câmera tática legível para observabilidade:

1. a fábrica é o foco e o viewport auto-enquadra as entidades construídas;
2. terrain/resource layers dão contexto sem competir com máquinas e logística;
3. o grid de debug fica fora do modo padrão;
4. recursos são patches contínuos, não uma matriz de quadrados;
5. sprites de mundo reais têm prioridade sobre ícones de inventário;
6. labels aparecem sob demanda por hover/click, não permanentemente;
7. estados físicos não são animados quando o Factorio está pausado entre ações FLE;
8. o dashboard diferencia infraestrutura inexistente de falha de renderização. No
   checkpoint v0.6 existem 8 transport belts persistidos e a topologia aparece no mapa;
9. controles avançados continuam recolhidos para preservar hierarchy visual;
10. motion é usado para feedback de UI (timeline, progresso, live state, frame transition),
    com respeito a prefers-reduced-motion.

A navegação do mapa suporta pan, zoom, reset, double-click para fit e fullscreen. Clicar em
uma máquina abre uma ficha contextual com prototype, tipo, posição e direção.

## Production Statistics

O bloco de produção segue o modelo do menu P do Factorio. Ele lê diretamente
LuaFlowStatistics.get_flow_count da surface corrente, em vez de reconstruir taxas por
diferença dos snapshots do dashboard.

Precisões expostas:

- 5s
- 1m
- 10m
- 1h
- 10h
- 50h
- 250h

Cada precisão contém as mesmas 300 amostras que alimentam os gráficos do jogo. O endpoint
retorna Production e Consumption em itens/minuto, além da contagem correspondente à janela.

As barras horizontais também seguem a semântica do jogo: o item com maior taxa naquele lado
ocupa 100% da barra e os demais são proporcionais a ele. O dashboard começa com
iron ore, copper ore, coal, stone e uranium ore.

## Game View e Tactical View

O mapa possui dois modos explícitos:

- Game View: terreno, recursos e sprites do mundo sem grid analítico; hotspots ficam quase
  invisíveis até hover/focus.
- Tactical View: adiciona grid por tile, footprints das entidades e a última rota A*
  persistida no journal.

Os dois modos usam o mesmo snapshot real do Factorio; o toggle apenas muda a camada de
visualização. A imagem continua read-only e não executa ações no jogo.

No currículo v0.6 a primeira rota persistente contém 8 transport belts e 1 burner inserter.
A aceitação exigiu minério no chest terminal, não apenas placement bem-sucedido.

A etapa seguinte usa o corredor A* como buffer para smelting. No currículo validado, a
célula belt-fed produziu 74 iron plates em 32 s; o direct-feed anterior produziu 36 em
24 s. As taxas são 2.3125 e 1.5 plates/s, respectivamente, razão normalizada 1.54x.
A janela belt-fed correspondeu a 9.25 plates por belt segment. Esses números são evidência daquela execução,
não uma alegação de throughput assintótico.

O runner usa um lock de processo em runs/curriculum.lock. Execuções manuais e o serviço
systemd compartilham o mesmo script, evitando dois writers simultâneos no Factorio.

## Production Monitor v0.6

O gráfico preserva as 300 amostras nativas de LuaFlowStatistics. Para melhorar a leitura de
ciclos discretos, o sinal bruto é desenhado em baixa opacidade e uma EMA visual é sobreposta.
O eixo Y é escalado pela série suavizada; impulsos brutos continuam visíveis, mas não achatam
a curva útil.

O número no cabeçalho é rotulado como avg, porque get_flow_count sem sample_index retorna
a taxa média da janela selecionada. Os nomes dos recursos são exibidos por extenso.

## Research loop

O curriculum_runner usa o mesmo Factorio real, mas é um processo separado do observer:

    ./scripts/run_curriculum.sh

Fluxo atual:

    baseline
      -> UCB1 placement trials + rollback
      -> promote placement
      -> scale mining
      -> direct-feed smelting
      -> A* belt logistics
      -> belt-fed smelting

Em cada trial o mundo é alterado, medido e restaurado caso a hipótese não seja promovida.
O A* só é aceito se minério físico chegar ao chest terminal. O estágio belt-fed só é aceito
se houver produção real de iron plates.

O dashboard mostra separadamente o estado do processo de pesquisa. Se o processo estiver
ativo e o journal não for atualizado por mais de 45 s durante um estágio ativo, o KPI muda
para stalled. Quando o currículo termina, mostra completed · idle e a próxima hipótese,
em vez de deixar o último estágio parecendo ainda estar executando.

O script usa flock sobre runs/curriculum.lock; uma segunda execução simultânea é recusada.

## O que realmente aprende

O painel Capability truth table diferencia deliberadamente componentes com semânticas
diferentes:

- Qwen3-4B: inferência local com pesos estáticos; sintetiza lessons tipadas;
- UCB1 online: atualiza estatísticas a partir de trials físicos reais;
- UCB1/A* offline: mantém calibração quantitativa dos hiperparâmetros;
- knowledge memory: cresce com resultados aceitos, rejeições e correções determinísticas;
- spatial demonstrations: rotas A* aceitas são persistidas como exemplos supervisionados;
- CNN/attention: ainda não treinada; gate inicial de 250 demonstrações;
- residual neural world model: ainda não treinado; o world model atual é explícito.

No v0.6 foi detectada e corrigida uma lesson que comparava raw counts de janelas de 24 s e
32 s. A comparação válida normaliza por duração: direct-feed 1.5 plates/s, belt-fed
2.3125 plates/s, razão 1.5417x. A correction lesson supersede a conclusão anterior de 2.06x.

O patch v0.6.1 inclui o módulo de normalização e seu teste no próprio repositório, de modo
que um clone limpo possui todas as dependências internas usadas pelo curriculum runner.

## Serviço local

O Uvicorn permanece limitado ao loopback:

    http://127.0.0.1:8765/

O serviço é instalado a partir de:

    ops/systemd/factorio-ai-dashboard.service

## Tailscale

A publicação remota é feita como TCP forward do Tailscale Serve. O Uvicorn não é exposto na
interface LAN e o Caddy existente não é alterado.

    ./scripts/expose_dashboard_tailscale.sh

Na máquina Midasnet atualmente resulta em:

    http://midasnet.tail106aa2.ts.net:8765/

A rota é acessível apenas dentro da tailnet. Para removê-la:

    sudo tailscale serve --tcp=8765 off

## Métricas visíveis

- estado RCON/Factorio;
- modelo local e endpoint llama.cpp;
- número de entidades e tick;
- latência do observer;
- produção/consumo nativos por item e por janela temporal;
- progresso físico do currículo;
- reward e trials do learner online;
- learner offline e melhor hiperparâmetro observado;
- sweep A*: turn penalty versus custo da busca;
- memória de conhecimento;
- configuração de planner, learner e LLM.

## v0.9 research analytics

The dashboard separates operational state from scientific evidence.

- Generation Health shows the active generation, incumbent, stage completion, route delta and
  open-play status.
- Research Summary keeps the previous closed generation visible while the next arena runs.
- Generational Metrics plots route cost, capability count and failed-stage count separately.
- Production DAG renders the rate-balanced recipe plan for the active frontier, falling back to
  the latest lab generation while open-play is active.
- WIP / Safety Stock shows material reserve, starvation and typed counterexample buffers.
- Model Evaluation Matrix compares learned models against explicit baselines and shows advisory,
  proposal or control eligibility.
- Knowledge quality reports LLM-verified lessons versus deterministic fallbacks.

The Research Loop KPI distinguishes lab generation, model training, selection transition and
open-play validation; neural training after a closed curriculum must not appear idle.

## Physical autonomy panel

The AUTONOMY / SURVIVAL panel is grounded in live Factorio entities,
LuaFlowStatistics, and the instrumented transactional executor.

It displays:

- autonomy level and score;
- zero-intervention soak duration;
- committed manual logistics calls after bootstrap;
- entities without fuel or power;
- physical gates for fuel distribution, electric distribution and smelting logistics;
- whether coal, iron and copper chains are live;
- whether intervention counters are instrumented or unavailable for a legacy run.

CAPABILITY SURVIVAL uses commissioned for previously demonstrated capability and
autonomous only when current physical evidence supports it. A lab champion is shown
separately from an open-play validated champion.
