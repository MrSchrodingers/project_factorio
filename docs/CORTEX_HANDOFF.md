# Cortex Research — Handoff Operacional

> **Chat/modelo com zero contexto:** começar por
> `docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md`. Ele contém a fundamentação teórica, roadmap
> F0–F12, fontes de verdade, estado operacional, interpretação do dashboard, protocolo
> SentinelX/GitHub, F2/F3 fechadas e o checkpoint ativo F4-B -> F4-C.

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F4 ACTIVE em F4-B / SHADOW — F3 COMPLETE; continuous authority OFF**
- Branch: `research/cortex-v1`
- Baseline imutável de origem: `74a1bf9c0f8792a68d7252b11d477835ec93d508`
- Tag baseline publicada: `cortex-pre-research-baseline-20260923`
- Handoff histórico: `docs/HANDOFF-CORTEX.md`
- Runtime: a última mutação live Cortex permanece F2-G4B. F3/F4 são SHADOW/offline; não há scheduler/grant contínuo. Evolution está inactive+disabled.

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

## Snapshot operacional histórico pré-transição — 2026-09-23 23:59 -03

- `factorio-ai-evolution.service`: active/running
- `factorio-ai-dashboard.service`: active/running
- `factorio-ai-llm.service`: active/running
- LLM local: Qwen3-4B via llama.cpp :18081
- RSS reportado do serviço LLM: ~8 GB — risco de memória a tratar na F1.

Esse bloco é histórico e NÃO representa os serviços atuais.

## Estado operacional atual revalidado em 2026-09-25

- branch: research/cortex-v1;
- F4-B closure publicada: 9a23b661c86ebd2eddb37eec2e1f3d8198c8b9c3;
- UI/status hardening implementation: 0c94090f91ad43c1a3a47a6bec25f377f9e56e00;
- evolution: inactive+disabled;
- active curriculum/open-play/evolution/Cortex runner processes: nenhum;
- phase-state: F4 / active / F4-B;
- next checkpoint: F4-C;
- F4 Exit Gate: aberto;
- confirmatory seeds 20261101–20261110: todas pending, zero running/completed;
- WORLD live: RCON conectado e 0 player-force entities no estado observado;
- 0 entities é estado físico observado. Não inferir renderer travado;
- G97/curriculum/UCB/model metrics são histórico congelado quando research_runner.active=false.

## Próxima ação

**F4-C — causal memory ablation + held-out transfer benchmark.**

F4-B está validada em SHADOW:

- implementation: a4ff558eccd9df0898fef4138a75fa1b55976a8d;
- canonical artifact: runs/audits/cortex_f4b_memory_retrieval.json;
- artifact SHA-256:
  5fc37b0cee5f121c5ff6b6054fc45f4b0a09bad851e34793958bdd3dc1c5a801;
- F4-A source artifact hash matches exactly;
- canonical memory DB: 222 items / 759 occurrences;
- DB quick_check=ok;
- database manifest before=after:
  e6aa69816fe992f6b2a6afc8aff529fa5f1572106ca0939cee830af5a6cf3399;
- four canonical retrieval queries PASS;
- structural scope mismatch is fail-closed;
- lexical similarity changes rank inside the same stage scope;
- exact symptom retrieval ranks the empirical fuel-resupply procedure first;
- counterexamples obey explicit stage+phase scope;
- repeated semantic items=70;
- semantic duplicate support=432;
- repeated counterexample items=10;
- procedural confidence items=1;
- non-destructive decay probe:
  low-support weight=0.3339200384203678,
  high-support weight=0.998542956000045;
- authority=shadow;
- world_mutation=false;
- FLE/RCON/WorldLease/execution grant unused;
- full gate: 1520 core/FLE + 2 PyTorch PASS plus static/build gates.

F4-B proves retrieval/consolidation/decay mechanics, not memory benefit.

F4-C must define the same held-out transfer tasks under two conditions:

1. memory available;
2. explicit memory ablation.

The split must prevent future-data leakage and must separate training/source runs from evaluation
runs. F4 closes only if memory removal causes statistically detectable performance loss. A lookup
benchmark alone is insufficient evidence of game-level benefit.

A auditoria preliminar de elegibilidade mostrou que os seeds exploratórios 20261001–20261005 ainda
não formam um benchmark causal adequado: repairs repetem essencialmente dois symptom/action
families, e cada seed contém somente uma spatial demo com o mesmo start/goal e route_cost=8.25.
Os 85 counterexamples globais / 33 signatures podem informar o design, mas 85 runs não equivalem a
85 independent seeds. Ver docs/CORTEX_PHASE4_CAUSAL_ABLATION_PROTOCOL.md.

Ainda não executar confirmatory seeds.
Não habilitar evolution.
Não conceder continuous autonomous authority.
Não usar as seeds 20261101–20261110 para tuning do protocolo.

## Protocolo de retomada após interrupção

Não inferir continuidade pela tela. Executar na ordem:

1. git status/HEAD/origin em /srv/factorio-ai-lab;
2. ler BUILD_INFO do runtime científico;
3. ler BUILD_INFO do runtime do dashboard;
4. confirmar factorio-ai-evolution inactive+disabled;
5. verificar processos de runner/evolution/Cortex;
6. regenerar runs/cortex_phase_state.json;
7. consultar /api/context, /api/world e WebSocket;
8. conferir artifacts/docs/checkpoints;
9. obedecer resume.do_not_start_another_seed.

Em F4-B/F4-C, do_not_start_another_seed=true significa NÃO lançar seed até o protocolo causal estar
congelado e elegível.

O runtime científico histórico da corrected baseline permanece pinado em
95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac. Source/dashboard podem avançar separadamente.

scripts/launch_corrected_baseline_seed.py pertence ao protocolo de baseline. Não deve ser usado
automaticamente durante F4-C. Qualquer novo non-confirmatory seed de F4-C deve nascer de
protocolo/manifest próprio, congelado e testado.

Prompt canônico para a próxima sessão: docs/CORTEX_NEXT_SESSION_PROMPT.md.
Detalhes de continuidade: docs/CORTEX_CONTINUITY_PROTOCOL.md.

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
