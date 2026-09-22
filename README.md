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

## Estado do marco v0.11.0

A v0.11 consolida o laboratório como sistema de pesquisa geracional orientado por evidência
física, com conhecimento canônico extraído do runtime do Factorio e gates estruturais de
maturidade.

O runtime continua usando Factorio 2.0.73 + FLE, Qwen3-4B local e execução transacional, mas
agora existem seis camadas de aprendizado e validação separadas:

1. evolução de engenharia — champion/challenger com genome estrutural, survival gates e rollback;
2. modelo de mundo — ESN e GRU PyTorch treinados sobre telemetria temporal e avaliados por
   holdout de gerações inteiras;
3. política espacial — MLP e Transformer/attention aprendem de demonstrações A* e competem por
   rollout/custo relativo ao A*;
4. conhecimento generativo — Qwen sintetiza hipóteses/lessons, mas um verifier determinístico
   rejeita números e taxas sem suporte nos fatos medidos;
5. conhecimento canônico do jogo — receitas, tecnologias, máquinas e dependências são extraídas
   dos prototypes do Factorio 2.0.73 e usadas pelo Production DAG quando o runtime está disponível;
6. topologia física — extração, belts, inserters, processamento, buffers, energia e fluidos formam
   um grafo observado, com starvation e cobertura até processamento entrando nos survival gates.

O currículo lab-play possui 16 estágios, avançando de iron mining até electronic circuits,
logistic science e otimização destrutiva/rebuild. Green science usa um DAG rate-balanced de
receitas do Factorio 2.0.73; matéria-prima e intermediários deixam de ser tratados como sobras
ocasionais de inventário.

A seleção não usa uma soma de taxas heterogêneas. Ferro/s, copper/s, science/s e outros fluxos
permanecem métricas separadas. Promoção exige retenção de capabilities, ausência de novas falhas
e melhoria em dimensões comparáveis.

### Estado experimental atual

- champion do laboratório: geração G6; este é um lab champion, não um autonomous champion;
- gerações recentes preservam iron/coal/copper/power/red science e convergem para electronic
  circuits;
- milhares de amostras temporais reais alimentam o world model;
- o GRU está treinado, mas permanece fora do controle quando não vence persistence de forma
  consistente no holdout entre gerações;
- a política espacial neural recebe autoridade somente conforme os gates contra A*;
- não existe ainda um open-play validated champion; esse artefato só pode ser criado quando
  o gate final registra closed-loop autonomy com zero logística manual na janela de soak;
- o open-play prioriza agora Electric Mining Transition antes do scale-up industrial pesado:
  commissioning manual → Automation → mineração elétrica → coal/iron/copper físicos → science;
- harvest, insert e extract são medidos separadamente como intervention debt; tentativas
  rejeitadas e ações commitadas não são misturadas;
- milestones de carvão e vapor são rotulados como commissioning até que a fábrica sobreviva
  ao soak físico sem intervenção;
- open-play usa inventário vazio e a árvore tecnológica real; fallback de navegação causado por
  limitação do FLE é registrado como assisted navigation, nunca como validação espacial estrita.

### Control plane

O dashboard v0.11 inclui Generation Health, Champion vs Challenger, Generation Report,
tendências geracionais, Production DAG, grafo canônico do jogo, topologia física viva,
WIP/safety stock, starvation, matriz de modelos e Production/Consumption nativo. O mapa usa
viewport em coordenadas reais do mundo; pan/zoom requisitam uma nova janela ao Factorio em vez
de apenas transformar uma imagem fixa. Séries de produção discretas são agregadas antes do plot
para reduzir aliasing visual de buckets sub-segundo.

A arquitetura de ML/LLM está detalhada em docs/ML_ARCHITECTURE.md e a seleção evolutiva em
docs/EVOLUTION.md.

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

    # Uma geração evolutiva completa: lab -> treino -> selection -> open-play
    ./scripts/run_evolution_loop.sh --generations 1

    # Treino/eval dos modelos recorrentes
    ./scripts/train_recurrent_world_model.sh

    # Treino/eval da política espacial
    ./scripts/train_spatial_policy.sh

    # Currículo lab isolado
    ./scripts/run_curriculum.sh

    # Dashboard manual
    ./scripts/run_dashboard.sh

    # Publicar dashboard somente na tailnet
    ./scripts/expose_dashboard_tailscale.sh

## Próximos experimentos

1. resolver electronic circuits usando material allocation/DAG em vez de estoques incidentais;
2. fechar o primeiro open-play production/technology validated champion;
3. ampliar o catálogo de receitas e allocator para green science, mall/bus e produção elétrica;
4. coletar mais gerações independentes para o GRU atingir o gate cross-generation;
5. evoluir políticas espaciais de imitation para DAgger e depois RL onde A* não for suficiente;
6. expandir destructive rebuild para células industriais e medir throughput/área/WIP;
7. adicionar perturbações dinâmicas e biters após a fábrica autônoma manter capacidades sob
   múltiplos seeds.

Consulte docs/ARCHITECTURE.md, docs/DASHBOARD.md, docs/FLE_RUNTIME.md,
docs/LLM_RUNTIME.md, docs/RESEARCH_BASELINE.md e docs/ROADMAP.md.
