# Cortex Research — Handoff Operacional

> **Chat/modelo com zero contexto:** começar por
> `docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md`. Ele contém a fundamentação teórica, roadmap
> F0–F12, fontes de verdade, estado operacional, interpretação do dashboard, protocolo
> SentinelX/GitHub, o fechamento F2-G5 e a próxima fase F3.

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F3 COMPLETE em F3-C — F4 READY / NOT STARTED; continuous authority OFF**
- Branch: `research/cortex-v1`
- Baseline imutável de origem: `74a1bf9c0f8792a68d7252b11d477835ec93d508`
- Tag baseline publicada: `cortex-pre-research-baseline-20260923`
- Handoff histórico: `docs/HANDOFF-CORTEX.md`
- Runtime: F2-E possui EXECUTE apenas por chamada explícita no adapter; não há scheduler/grant contínuo. Evolution está disabled após reboot.

## Regra de retomada

1. Ler `docs/CORTEX_RESEARCH_PROGRAM.md`.
2. Executar `git status --short --branch`.
3. Verificar commit/branch contra este arquivo.
4. Ler a seção da fase ativa e os últimos checkboxes.
5. Conferir `runs/research_state.json` e `runs/runtime_heartbeat.json`.
6. Não assumir que serviços/runs permanecem no estado descrito aqui.
7. Não avançar de fase sem preencher Evidence / Tests / Commit / Decision.

## Condição encontrada antes da F0

Antes da transição havia mudanças não commitadas pré-existentes em:

- `src/factorio_ai_lab/experiments/curriculum_runner.py`
- `src/factorio_ai_lab/planning/delivery.py`
- `tests/test_coal_distribution.py`
- `tests/test_ore_handoff.py` (untracked)
- `tests/test_trunk_delivery.py` (untracked)

Essas mudanças pertencem à evolução logística anterior e **não devem ser absorvidas
silenciosamente pelo commit da F0**.

## Estado operacional observado em 2026-09-23 23:59 -03

- `factorio-ai-evolution.service`: active/running
- `factorio-ai-dashboard.service`: active/running
- `factorio-ai-llm.service`: active/running
- LLM local: Qwen3-4B via llama.cpp :18081
- RSS reportado do serviço LLM: ~8 GB — risco de memória a tratar na F1.

## Próxima ação

**F4 — Cognitive Memory and Consolidation. F4 está READY / NOT STARTED.**

F3-C fechou o Exit Gate F3 em SHADOW:

- implementation: 6a209527a14cc9a8eebb12c4d8ac403884fde281;
- canonical artifact: runs/audits/cortex_f3c_paired_shadow_comparison.json;
- artifact SHA-256: 13b00f67b7014a5f3af3bf068e9381bc563100c4a984688ca88ab23795ba5f01;
- paired historical objectives: 29;
- every pair exposes at least two candidates;
- candidate set invariant across compared policies: 29 / 29;
- canonical fixed rule, computed without historical action label: 29 / 29 agreement;
- fixed legacy-preference policy: 29 / 29 agreement;
- rebuild-preference policy: 1 / 29 agreement;
- observable policy divergence: 28 / 29;
- observed structural actions executed: 0 / 29;
- observed numeric rewards: 0 / 29;
- authority=shadow;
- world_mutation=false;
- FLE/RCON/WorldLease/execution grant: unused;
- full gate: 1493 core/FLE + 2 PyTorch PASS plus static/build gates.

F3 Exit Gate: PASS.
The sequence is no longer required to live inside a stage handler for the compared decisions.

This does not claim that the divergent policy is better. Learned-policy superiority belongs to F5
and requires holdout evidence.

F4 contract:

- working memory;
- episodic store;
- semantic store versioned;
- procedural skill library;
- hybrid structural/similarity retrieval;
- counterexamples as first-class memory;
- confidence/support/validity scope;
- consolidation;
- forgetting/decay;
- memory ablation;
- cross-seed transfer.

F4 Exit Gate requires statistically detectable transfer loss when memory is removed. Logging alone
is insufficient.

Ainda não executar confirmatory seeds.
Não habilitar evolution.
Não conceder continuous autonomous authority.
Não iniciar live Cortex execution apenas para abrir F4.

## Protocolo de retomada após interrupção

Não inferir continuidade pela tela. Executar na ordem:

1. git status em /srv/factorio-ai-lab;
2. ler BUILD_INFO do runtime científico;
3. ler BUILD_INFO do runtime do dashboard;
4. confirmar factorio-ai-evolution inativo durante baseline;
5. consultar /api/context;
6. conferir manifest/result da última seed;
7. conferir checkboxes deste handoff e do programa;
8. executar somente a próxima seed ainda não marcada.

Durante a série exploratória o runtime científico deve permanecer em
95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac mesmo que source e dashboard avancem.

Checkpoint mecânico: executar `scripts/cortex_phase_state.py --write` e obedecer `resume.action`.
Novas seeds devem ser iniciadas por `scripts/launch_corrected_baseline_seed.py`, que desacopla a
execução da sessão SentinelX, valida SHA + release-root contra o protocolo, recusa seed duplicada
ou evolution concorrente e grava automaticamente o snapshot global de isolamento pré-run.
Detalhes: docs/CORTEX_CONTINUITY_PROTOCOL.md.

## Evidência de fechamento da F0

F0: **PASS**.

- commit científico: `767b9238202b021ff1c5679eeca4d19f72b39f12`;
- branch publicada: `origin/research/cortex-v1`;
- tag publicada: `cortex-pre-research-baseline-20260923`;
- 50 testes de dashboard: PASS;
- compileall dashboard: PASS;
- node --check: PASS;
- probes HTTP das duas vertentes: PASS;
- diff --check: PASS;
- SentinelX: `sxc_4557STHZ` revisão 5.

A F1 deve preservar as mudanças logísticas pré-existentes registradas no manifest e tratá-las
explicitamente antes de resetar qualquer baseline.
