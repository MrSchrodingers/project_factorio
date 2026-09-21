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

## Estado do marco v0.3.0

O laboratório já executa o Factorio 2.0.73 via FLE, mantém checkpoints transacionais e possui
um control plane web com telemetria ao vivo. O LLM baseline é Qwen3-4B Q4_K_M local em
llama.cpp; nenhum provider pago é necessário.

Componentes ativos:

- Factorio/FLE: 127.0.0.1:27000 RCON;
- dashboard: 127.0.0.1:8765;
- Qwen/llama.cpp: 127.0.0.1:18081;
- acesso do dashboard pela tailnet: http://midasnet.tail106aa2.ts.net:8765/;
- learner UCB1 para seleção quantitativa do turn penalty do A*;
- router de modelos que permite apenas backends local ou free;
- Run 001 construtiva: burner mining drill -> wooden chest com validação transacional;
- endpoint /api/run e estado da execução atual no control plane.

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
    PYTHONPATH=src .venv-fle/bin/python -m factorio_ai_lab.experiments.run_iron_miner       --seed 20260921 --settle-seconds 20

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
