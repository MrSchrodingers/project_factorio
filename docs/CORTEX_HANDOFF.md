# Cortex Research — Handoff Operacional

> **Chat/modelo com zero contexto:** começar por
> `docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md`. Ele contém a fundamentação teórica, roadmap
> F0–F12, fontes de verdade, estado operacional, interpretação do dashboard, protocolo
> SentinelX/GitHub e o estado G4B e próximo checkpoint F2-G5.

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-G4B concluída — F2-G5 baseline-only enforcement é o próximo checkpoint; F2 ativa; F3 bloqueada**
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

**F2-G5 — formalizar o runner legado como baseline-only.**

F2-G4B executou exatamente uma Option live não-confirmatória pelo caminho genérico do Cortex:

- implementation commit: 794963b435bff616042ca0a6e6f278ead315e5e0;
- temporal fix: aaf10beb5b5ec11b7b28e3619823b02b0a465b59;
- live run: cortex-f2g4b-20260925T014418Z;
- seed 424242, não-confirmatória;
- live artifact SHA-256: fb9b69b38a3446dd956ebf529f1888bebfb670cfe59fa8fd8b24b74030f0fc95;
- temporal audit SHA-256: 21cdcbe0e60751952600ae1edb26ab4d0d94e72c9fff5d18e3d83f1f023e52d1;
- exatamente 1 execution attempt;
- automatic_retry=false;
- continuous_authority=false;
- grant persistente one-shot;
- scope ligado ao lease_id real e re-atestado imediatamente antes do consume;
- grant consumido duravelmente antes da mutação;
- lease liberado ao final;
- Option accepted, changed_world=true, transaction_committed=true;
- todos os hard postconditions satisfeitos;
- coverage 0 -> 1;
- processor output 0 -> 13 iron plates;
- producers reaching processor 0 -> 1;
- furnace final no_fuel;
- functional_accept=true;
- sustained_operation=false.

Counterexample temporal preservado:

- artifact original: ticks_before=21840, ticks_after=7800, tick_measurement_status=invalid_rewound;
- causa auditada: FLE 0.4.3 restaura game_state antes da Action e o reset zera storage.elapsed_ticks;
- nenhum rerun foi feito;
- o artifact live não foi reescrito;
- o fix futuro usa FLEStep.info.ticks como epoch local em accepted checkpoint actions.

Gates:

- G4B implementation: 1459 core/FLE + 2 PyTorch PASS;
- temporal fix: 1460 core/FLE + 2 PyTorch PASS;
- G4B control plane: 1460 core/FLE + 2 PyTorch PASS;
- Ruff/compileall/JS/TS/Vite/whitespace: PASS.

Documento canônico:

docs/CORTEX_PHASE2_LIVE_OPTION_CANARY.md

Checklist F2 após G4B:

- transactional execution universal: atendido;
- initial Option: atendido;
- functional chain live por Option/API sem curriculum_runner: atendido;
- runner legado executável apenas como baseline: ainda aberto.

F2-G5 deve impedir que caminhos Cortex importem/despachem curriculum_runner ou stage handlers
legados como authority. O runner legado deve continuar disponível para experimentos rotulados
explicitamente como baseline.

Não executar confirmatory seeds.
Não habilitar evolution.
Não conceder continuous autonomous authority.
Não repetir o canário G4B.
F3 permanece bloqueada.
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
