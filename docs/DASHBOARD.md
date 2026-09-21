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

Os assets são obtidos da distribuição oficial Linux demo do Factorio 2.0.73 e usados somente
na máquina de runtime. Eles não são versionados nem redistribuídos pelo repositório. O Git
contém apenas o código que resolve e renderiza esses arquivos locais.

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

## Research loop

O curriculum_runner usa o mesmo Factorio real, mas é um processo separado do observer:

    ./scripts/run_curriculum.sh

Fluxo atual:

    baseline -> trials UCB1 -> rollback -> promoção -> scale mining -> smelting probe

Em cada trial o mundo é alterado, medido e depois restaurado caso a hipótese não seja
promovida. O dashboard mostra o estágio, timeline, reward, braço selecionado, métricas e
conhecimento sintetizado.

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
