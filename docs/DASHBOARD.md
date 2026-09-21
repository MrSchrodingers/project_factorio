# Dashboard e control plane

O dashboard é uma camada de observabilidade read-only sobre o laboratório. O processo web não
executa ações de gameplay e não chama reset do ambiente. O estado do mundo é lido do FLE e
convertido em telemetria estruturada.

## Endpoints

- / — frontend HTML/CSS/JS sem CDN.
- /api/status — saúde de Factorio, LLM, memória e configuração ativa.
- /api/world — snapshot atual de entidades e produção.
- /api/history — série temporal coletada durante conexões WebSocket.
- /api/learning — estado do experimento UCB1.
- /api/experiments/routing — agregação do sweep A*.
- /api/config — parâmetros runtime editáveis.
- /ws/live — telemetria ao vivo.

O mapa 2D é desenhado em Canvas usando coordenadas de entidades do FLE. Assim que o agente
começar a construir, belts, máquinas e infraestrutura aparecem sem depender de screenshot ou
visão computacional.

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

## Métricas atualmente visíveis

- estado RCON/Factorio;
- modelo local e endpoint llama.cpp;
- número de entidades e tick;
- latência do observer;
- memória do host;
- learner e melhor hiperparâmetro observado;
- reward por episódio;
- sweep A*: turn penalty versus custo da busca;
- telemetria ao vivo;
- configuração de planner, learner e LLM.

A visualização ainda não pretende reproduzir os sprites do Factorio. O objetivo deste marco é
uma representação geométrica e causal adequada a debug, métricas e replay.
