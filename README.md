# Factorio AI Lab — Cortex Research

Laboratório de pesquisa em **agência cognitiva, aprendizado contínuo, memória, otimização de
engenharia e NeuroAI** usando Factorio como ambiente experimental.

O objetivo do projeto não é simplesmente automatizar uma sequência capaz de lançar um foguete.
A pergunta científica é se um agente consegue **aprender a projetar, operar, reorganizar e
otimizar fábricas**, formar memória reutilizável, transferir estratégias entre mundos e melhorar
com experiência sem receber do programador a sequência de ações que constitui a solução.

> **Documento canônico:** [docs/CORTEX_RESEARCH_PROGRAM.md](docs/CORTEX_RESEARCH_PROGRAM.md)
> **Handoff operacional:** [docs/CORTEX_HANDOFF.md](docs/CORTEX_HANDOFF.md)
> **Diagnóstico da arquitetura anterior:** [docs/HANDOFF-CORTEX.md](docs/HANDOFF-CORTEX.md)
> **F1 — integridade e isolamento:** [docs/CORTEX_PHASE1_INTEGRITY.md](docs/CORTEX_PHASE1_INTEGRITY.md)
> **F1-B — protocolo da baseline:** [docs/CORTEX_PHASE1_BASELINE_PROTOCOL.md](docs/CORTEX_PHASE1_BASELINE_PROTOCOL.md)
> **F1-B — seed 20261001:** [docs/CORTEX_PHASE1_BASELINE_SEED_20261001.md](docs/CORTEX_PHASE1_BASELINE_SEED_20261001.md)
> **F1-B — seed 20261002:** [docs/CORTEX_PHASE1_BASELINE_SEED_20261002.md](docs/CORTEX_PHASE1_BASELINE_SEED_20261002.md)
> **F1-B — seed 20261003:** [docs/CORTEX_PHASE1_BASELINE_SEED_20261003.md](docs/CORTEX_PHASE1_BASELINE_SEED_20261003.md)
> **F1-B — seed 20261004:** [docs/CORTEX_PHASE1_BASELINE_SEED_20261004.md](docs/CORTEX_PHASE1_BASELINE_SEED_20261004.md)
> **F1-B — seed 20261005:** [docs/CORTEX_PHASE1_BASELINE_SEED_20261005.md](docs/CORTEX_PHASE1_BASELINE_SEED_20261005.md)
> **F1 — relatório estatístico:** [docs/CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md](docs/CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md)
> **F2 — Action Ontology:** [docs/CORTEX_PHASE2_ACTION_ONTOLOGY.md](docs/CORTEX_PHASE2_ACTION_ONTOLOGY.md)
> **F2-B — Legacy parity:** [docs/CORTEX_PHASE2_LEGACY_PARITY.md](docs/CORTEX_PHASE2_LEGACY_PARITY.md)
> **F2-C — Structural planning:** [docs/CORTEX_PHASE2_STRUCTURAL_PLANNING.md](docs/CORTEX_PHASE2_STRUCTURAL_PLANNING.md)
> **F2-D — Resource identity / preparation:** [docs/CORTEX_PHASE2_STRUCTURAL_PREPARATION.md](docs/CORTEX_PHASE2_STRUCTURAL_PREPARATION.md)
> **F2-E — Controlled transactional execution:** [docs/CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md](docs/CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md)
> **F2-F — Functional dependency completion:** [docs/CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY.md](docs/CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY.md)
> **F2-F2 — Functional dependency composition:** [docs/CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY_COMPOSITION.md](docs/CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY_COMPOSITION.md)
> **F1-B — storage hardening:** [docs/CORTEX_F1_STORAGE_HARDENING.md](docs/CORTEX_F1_STORAGE_HARDENING.md)
> **Dashboard / evidence scope:** [docs/CORTEX_DASHBOARD_EVIDENCE_SCOPE.md](docs/CORTEX_DASHBOARD_EVIDENCE_SCOPE.md)
> **Dashboard / tmp hardening:** [docs/CORTEX_DASHBOARD_TMP_HARDENING.md](docs/CORTEX_DASHBOARD_TMP_HARDENING.md)
> **Continuidade / retomada:** [docs/CORTEX_CONTINUITY_PROTOCOL.md](docs/CORTEX_CONTINUITY_PROTOCOL.md)

## Estado do programa

**Cortex Research Architecture v0.1 — F2-F2 validada. O counterexample no_fuel de F2-E agora
é tratado como dependência tipada: consumo e fuel compatibility são medidos, plan_supply decide
cobertura real e carried fuel vira operação semântica fuel_processor. F2-F3 será autorizado somente
após publicação do commit clean; continuous authority e F3 seguem proibidas.**

As seeds confirmatórias 20261101–20261110 permanecem congeladas e não executadas; serão usadas
posteriormente para avaliação pareada do Cortex, sem tuning nelas.

A arquitetura anterior permanece disponível como baseline. Ela possui excelente instrumentação,
solvers e mecanismos de segurança, mas o caminho de decisão principal ainda é dominado por
runners escritos à mão. A refatoração Cortex desloca essa autoridade para um loop cognitivo
medido.

## Duas vertentes

### A. Cortex Híbrido de Engenharia

Combina:

- espaço de ações e *options* tipados;
- memória de trabalho, episódica, semântica e procedural;
- LLM local como gerador de hipóteses/programas;
- política aprendida para escolha;
- ferramentas determinísticas de engenharia;
- Graph World Model residual;
- quality-diversity;
- ALNS/LNS para reconfiguração industrial;
- avaliação externa e verificável.

Fluxo alvo:

```text
observe → belief update → goal → memory recall → candidates
        → feasibility → counterfactuals → policy → act
        → verify → credit assignment → learn
```

### B. Cortex-Fly / Connectomic Prior

Pesquisa NeuroAI inspirada por FlyWire/FlyGM. O connectoma de *Drosophila* é tratado como um
**prior topológico falsificável**, não como uma simulação presumida da mente de uma mosca.

A topologia real será comparada contra controles degree-preserving rewired, random graph, GNN,
Graph Transformer e MLP sob orçamento pareado.

## O que preservamos da arquitetura atual

Ferramentas corretas continuam sendo ferramentas do agente:

- Factorio Learning Environment;
- execução transacional e rollback;
- telemetria causal;
- catálogo real de receitas/tecnologias;
- Production DAG;
- A* e futuros solvers de rede;
- placement/delivery/resupply;
- material ledger;
- factory graph;
- survival analysis;
- MAP-Elites;
- modelos neurais como challengers com gates.

O projeto não pretende fazer uma rede neural redescobrir receitas ou A* apenas para parecer mais
“AI”. O aprendizado entra onde há uma **decisão** a aprender ou um residual que métodos explícitos
não explicam.

## Regra experimental

Um componente treinado não recebe autoridade automaticamente.

```text
offline → shadow → proposal → control-eligible
```

Promoção exige baseline, holdout, multi-seed, ablação, provenance e incerteza.

## Métrica operacional central

A principal medida física é **produção autônoma sustentável durante janela de holdout sem
intervenção**. “Stage completed” e produção causada por chamadas manuais não substituem essa
evidência.

Métricas adicionais incluem throughput, área, distância logística ponderada por fluxo,
congestionamento, WIP, energia, custo de rebuild, intervenção, expansão futura, calibration,
regret, sample efficiency, transferência e recuperação a perturbações.

## Ambiente atual

Baseline de software:

- Factorio 2.0.73;
- FLE 0.4.3;
- Python 3.12;
- Qwen3-4B Q4_K_M via llama.cpp;
- host inicial CPU-only (8C/16T, ~15 GiB RAM);
- FastAPI dashboard;
- execução baseada em checkpoints.

A baseline pré-Cortex foi marcada no commit
`74a1bf9c0f8792a68d7252b11d477835ec93d508`.

O runtime científico não executa mais diretamente o checkout de desenvolvimento. Releases limpas
são materializadas por SHA em /srv/factorio-ai-runtime/releases/<sha> e o symlink current é
trocado atomicamente; estado mutável permanece em /srv/factorio-ai-lab/runs.

O dashboard possui runtime separado em /srv/factorio-ai-dashboard-runtime. Durante F1-B ele pode
avançar sem alterar o runtime científico congelado da baseline. O evidence scope é declarado por
FACTORIO_AI_DASHBOARD_SCOPE; mundo físico e evidência experimental são rotulados separadamente.
Dashboard F1-B atualmente deployado em 8ce05ba3; a baseline científica permanece pinada em 95c34a53.

## Estrutura

```text
src/factorio_ai_lab/
├── agents/          LLM/router/advisors
├── learning/        memória, evolução, modelos, survival, repair
├── planning/        solvers e ferramentas de engenharia
├── integrations/    FLE / runtime
├── experiments/     baselines, arenas e runners herdados
└── dashboard/       observabilidade e cockpit científico

docs/
├── CORTEX_RESEARCH_PROGRAM.md   contrato científico e roadmap
├── CORTEX_HANDOFF.md            checkpoint curto para retomada
├── HANDOFF-CORTEX.md            diagnóstico da transição
└── pesquisa-metodologia.md      revisão metodológica anterior
```

## Testes

O projeto possui dois perfis porque runtime FLE e ambiente ML têm dependências distintas:

    cd /srv/factorio-ai-lab
    ./scripts/test_profiles.sh

Na F1-A o gate registrou 1283 testes core/FLE e 2 testes PyTorch aprovados, além de Ruff e compileall.

Para frontend:

    cd frontend
    npm run typecheck
    npm run build

## Continuidade

Não use a conversa como fonte de verdade. Use o repositório.

Toda fase do programa contém checkboxes, Exit Gate e campos de evidência. Um novo operador deve
começar por `docs/CORTEX_HANDOFF.md`, verificar a árvore Git e continuar apenas a fase ativa.

## Referências centrais

- FLE — https://arxiv.org/abs/2503.09617
- CoALA — https://arxiv.org/abs/2309.02427
- DreamerV3 — https://doi.org/10.1038/s41586-025-08744-2
- Voyager — https://arxiv.org/abs/2305.16291
- Agent Lightning — https://www.microsoft.com/en-us/research/publication/agent-lightning-v1-0-towards-harnessed-agentic-rl/
- FunSearch — https://doi.org/10.1038/s41586-023-06924-6
- AlphaEvolve — https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/
- FlyWire — https://doi.org/10.1038/s41586-024-07558-y
- FlyGM — https://arxiv.org/abs/2602.17997
- NeuroMechFly v2 — https://doi.org/10.1038/s41592-024-02497-y

## Licença

MIT, salvo datasets/modelos externos, que mantêm suas próprias licenças.
