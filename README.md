# Factorio AI Lab

Laboratório experimental para estudar agentes autônomos capazes de construir, diagnosticar,
otimizar e evoluir fábricas no **Factorio** com custo de inferência inicial igual a zero.

O projeto não assume que um LLM seja o controlador ideal de baixo nível. A arquitetura é híbrida:
algoritmos determinísticos resolvem geometria, fluxo e restrições; modelos neurais aprendem
políticas e heurísticas; um LLM local atua como planejador semântico, sintetizador de programas
e crítico de alto nível.

## Hipótese de pesquisa

Uma política hierárquica

`objetivo -> decomposição -> otimização -> roteamento -> execução -> verificação -> aprendizado`

deve superar um agente LLM monolítico em pelo menos três eixos:

1. taxa de sucesso sob orçamento fixo de computação;
2. eficiência espacial/material da solução;
3. capacidade de recuperação após falhas sem degradar o estado do mundo.

## Base externa

- **Factorio Learning Environment (FLE)**: ambiente Gym/REPL, tarefas lab-play/open-play,
  checkpoints e interface com o Factorio real.
- **Factorion**: referência de RL espacial com simulador rápido, SFT + PPO e política
  CNN + self-attention.
- **Factorio Runtime API 2.1**: fonte de verdade para integração do mundo real.

Não vendorizamos esses projetos. Integrações são feitas por adapters/dependências para manter
comparações reproduzíveis e separar claramente trabalho próprio de código externo.

## Arquitetura inicial

```text
Goal / Task
    |
    v
Hierarchical Orchestrator
    |------> Local LLM planner/critic (OpenAI-compatible endpoint)
    |------> Production optimizer (LP/MILP)
    |------> Spatial planner (A*/JPS/constraint search)
    |------> Learned policy/value model
    |
    v
Transactional Executor
    |
    +------> Fast abstract simulator
    |
    +------> FLE / Factorio ground truth
    |
    v
Telemetry + Replay Buffer + Evaluator
```

## Hardware-alvo inicial

O primeiro host é CPU-only. O baseline deve funcionar em 8 cores/16 threads e 16 GiB RAM.
Por isso o primeiro modelo sugerido é **Qwen3-4B GGUF Q4_K_M via llama.cpp**. Nemotron Nano
9B v2 pode entrar como benchmark secundário quantizado; modelos MoE de ~30B ficam fora do
baseline de RAM.

## Estado do marco v0.5.0

O laboratório executa Factorio 2.0.73 via FLE, mantém checkpoints transacionais e possui
um control plane web com telemetria ao vivo. O LLM baseline é Qwen3-4B Q4_K_M local em
llama.cpp; nenhum provider pago é necessário.

Componentes ativos:

- Factorio/FLE: 127.0.0.1:27000 RCON;
- dashboard: 127.0.0.1:8765;
- Qwen/llama.cpp: 127.0.0.1:18081;
- acesso do dashboard pela tailnet: http://midasnet.tail106aa2.ts.net:8765/;
- mapa raster ao vivo com terreno e ícones oficiais do Factorio carregados apenas no runtime
  local, com zoom/pan e sem redistribuir os assets no Git;
- monitor de Production/Consumption baseado diretamente em LuaFlowStatistics, com as 300
  amostras nativas do jogo e janelas 5s/1m/10m/1h/10h/50h/250h;
- learner UCB1 offline para o turn penalty do A*;
- learner UCB1 online que executa placements reais no Factorio com rollback transacional;
- memória de conhecimento estruturado sintetizada pelo Qwen local;
- router que permite apenas backends local ou free.

O primeiro currículo online validado executou 8 trials reais de placement, promoveu
east_near, persistiu uma segunda célula de mineração e aceitou uma célula de fundição que
produziu 36 iron plates. O próximo objetivo registrado pelo research loop é projetar a
extração por belts usando A*.

O marco v0.5.0 redesenha a experiência de observabilidade com foco em legibilidade do
mundo. O renderer deixou de usar ícones de inventário e grids de debug como visualização
principal: ele auto-enquadra a fábrica, usa sprites de mundo reais para as entidades
suportadas, compõe patches orgânicos de recursos, remove ruído natural do modo padrão e
mantém inspeção contextual por hotspot. A UI também diferencia explicitamente o que já
existe no checkpoint aceito do que é o próximo estágio de pesquisa; no estado atual há
zero belts persistidos e a logística por A* é a próxima etapa.

## Comandos principais

    cd /srv/factorio-ai-lab

    # Testes
    PYTHONPATH=src python3 -m unittest discover -s tests -v

    # Baseline determinístico
    PYTHONPATH=src python3 -m factorio_ai_lab.cli baseline

    # Sweep A*
    PYTHONPATH=src python3 -m factorio_ai_lab.experiments.routing_sweep       --seeds 100 --output runs/routing_sweep_100.csv

    # Aprendizado do hiperparâmetro de curva
    PYTHONPATH=src python3 -m factorio_ai_lab.experiments.learn_turn_penalty       --episodes 300

    # Primeira run construtiva no Factorio real
    PYTHONPATH=src .venv-fle/bin/python -m factorio_ai_lab.experiments.run_iron_miner \
      --seed 20260921 --settle-seconds 20

    # Currículo autônomo com aprendizado online + rollback
    ./scripts/run_curriculum.sh

    # Dashboard manual
    ./scripts/run_dashboard.sh

    # Publicar dashboard somente na tailnet
    ./scripts/expose_dashboard_tailscale.sh

## Próximos experimentos

1. gerar datasets de demonstrações do planner;
2. treinar política espacial supervisionada e comparar CNN, CNN+self-attention e GNN;
3. adicionar grafo de receitas e otimização de capacidade/produção;
4. fazer o LLM gerar planos tipados em vez de comandos livres;
5. acoplar executor transacional ao ciclo de auto-healing;
6. somente depois adicionar perturbações dinâmicas e insetos.

Consulte docs/ARCHITECTURE.md, docs/DASHBOARD.md, docs/FLE_RUNTIME.md,
docs/LLM_RUNTIME.md, docs/RESEARCH_BASELINE.md e docs/ROADMAP.md.
