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

## Estado do marco v0.6.0

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

O currículo v0.6 executa seis estágios reais no mesmo mundo transacional:

1. baseline de mineração;
2. 8 trials UCB1 de placement com rollback;
3. promoção da melhor segunda célula;
4. smelting direct-feed;
5. logística física planejada por A*;
6. smelting alimentado pela logística aceita.

Na validação de referência, northwest_edge foi promovido. O A* construiu 8 transport belts,
fez 1 curva, custo 7.25, expandiu 22 nós e entregou 37 iron ore no chest terminal. A cadeia
belt-fed produziu 74 iron plates em 32 s (2.3125/s), contra 36 em 24 s no direct-feed
(1.5/s): razão de taxa normalizada 1.5417x. Comparações de throughput usam duração explícita;
raw counts de janelas diferentes são rejeitados como métrica comparativa.

O marco v0.5.0 redesenhou a experiência de observabilidade com foco em legibilidade do
mundo: auto-enquadramento da fábrica, sprites de mundo reais, patches orgânicos de recursos,
grid removido do modo padrão e inspeção contextual.

O v0.6.0 adiciona a primeira logística persistente gerada pelo planner do projeto. O A*
planejou uma rota de 8 transport belts com 1 curva, custo 7.25 e 22 nós expandidos; a linha
foi aceita apenas após 37 iron ore chegarem ao chest terminal por um burner inserter.
O runner agora usa lock exclusivo para impedir duas pesquisas concorrentes no mesmo mundo.
O dashboard expõe heartbeat do curriculum runner e diferencia active, stalled e
completed/idle. O Qwen é identificado como inference com pesos estáticos: o aprendizado
persistente atual ocorre no UCB1, na knowledge memory tipada, nos planners calibrados e no
dataset de demonstrações espaciais. A política CNN/attention ainda não é treinada; o alvo
inicial configurado é 250 demonstrações aceitas.

O dashboard agora oferece Game View e Tactical View separados. Game View prioriza leitura
visual da fábrica; Tactical View adiciona grid, footprints e a rota A* registrada no journal.

O currículo também integrou essa logística a uma segunda célula de smelting: a validação
belt-fed produziu 74 iron plates em 32 s. O direct-feed anterior produziu 36 em 24 s;
a comparação normalizada é 2.3125 vs 1.5 plates/s, razão de throughput 1.54x.
A taxa belt-fed correspondeu a 0.289 plates/s por belt segment nessa topologia. O mundo
aceito permanece no Factorio após o runner encerrar e após restart do dashboard.

O runner operacional usa flock em runs/curriculum.lock; portanto apenas um processo possui
autoridade de escrita sobre o mundo por vez. O serviço systemd é versionado em
ops/systemd/factorio-ai-curriculum.service.

O Production Monitor também foi refinado: nomes completos de recursos, labels maiores,
média de janela rotulada corretamente e uma curva EMA legível sobre os 300 samples nativos,
com o sinal bruto preservado em baixa opacidade.

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
