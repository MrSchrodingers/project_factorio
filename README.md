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

## Estado do marco v0.8.0

O laboratório executa Factorio 2.0.73 via FLE com checkpoints transacionais, Qwen3-4B local,
planners determinísticos, aprendizado UCB1 e um control plane web em tempo real. Nenhum
provider pago é necessário.

A pesquisa agora é dividida em duas arenas:

1. Lab arena: ambiente FLE acelerado para experimentos quantitativos de placement, A*,
   logística, smelting, buffers, alocação de combustível e survival gates.
2. Open-play validation: inventário vazio e árvore tecnológica real. Somente um champion
   sobrevivente do lab pode entrar nessa arena.

O currículo do lab possui treze estágios:

1. baseline de mineração de ferro;
2. aprendizado online UCB1 de placement;
3. promoção da melhor célula;
4. smelting direto;
5. logística física por A*;
6. smelting alimentado por belt;
7. carvão endógeno com quarentena do bootstrap;
8. expansão para cobre;
9. smelting de cobre com carvão interno;
10. survival soak simultâneo de ferro, carvão, cobre e fundição;
11. energia a vapor;
12. manufatura elétrica;
13. automation science.

Cada execução é um challenger. Falhar um survival gate impede promoção. O primeiro champion
precisa completar os gates do lab; depois ainda precisa passar open_play para se tornar um
champion validado. A ausência de champion é um resultado experimental válido.

O carvão usa uma política explícita de safety stock: consumidores só podem retirar o excedente
acima da reserva mínima, enquanto o produtor recebe refuel operacional. Isso evita que cobre ou
smelting matem a própria cadeia de combustível.

No Factorio 2.0.73, a validação open-play respeita os gatilhos reais do início do jogo:
craftar 50 iron plates libera Steam Power; craftar 10 copper plates libera Electronics;
depois de ambos, craftar um lab libera Automation Science Pack. Só então red science e
Automation entram na fronteira.

O que aprende hoje:

- UCB1 aprende valores de placements em trials reais no Factorio;
- o learner offline calibra custos do A*;
- champion/challenger seleciona configurações por sobrevivência e fitness;
- knowledge memory retém lessons e counterexamples tipados;
- demonstrações espaciais aceitas alimentam o futuro dataset supervisionado.

Os pesos do Qwen3-4B permanecem estáticos. A CNN/RNN e o residual neural world model ainda
não são declarados como treinados: eles só entram quando houver dataset e hipótese temporal
suficientes.

O dashboard mostra Factory, Resources e Tactical views, Production/Consumption nativo do
LuaFlowStatistics, estado físico das máquinas, capability survival, safety stock de carvão,
Champion vs Challenger, progression frontier e knowledge memory. Recursos e entidades usam
coordenadas reais do Factorio e assets locais fornecidos pelo usuário; o terreno não aquático
ainda é texturizado/reconstruído, enquanto água e recursos são observados do mundo real.

A especificação completa da seleção evolutiva está em docs/EVOLUTION.md.

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

1. continuar coletando demonstrações A* reais até o gate inicial de 250 exemplos;
2. executar variantes transacionais de rota e otimizar throughput normalizado contra belts,
   curvas e área ocupada;
3. treinar política espacial supervisionada e comparar CNN, CNN+self-attention e GNN;
4. adicionar grafo de receitas e otimização de capacidade/produção;
5. fazer o LLM gerar planos tipados em vez de comandos livres;
6. acoplar um orquestrador persistente que consuma next_action sem resetar o checkpoint aceito;
7. ampliar o ciclo de auto-healing;
8. somente depois adicionar perturbações dinâmicas e insetos.

Consulte docs/ARCHITECTURE.md, docs/DASHBOARD.md, docs/FLE_RUNTIME.md,
docs/LLM_RUNTIME.md, docs/RESEARCH_BASELINE.md e docs/ROADMAP.md.
