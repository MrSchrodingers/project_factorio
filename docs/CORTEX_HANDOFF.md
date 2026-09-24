# Cortex Research — Handoff Operacional

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-D concluída — F2-E autorizada; authority live contínua ainda bloqueada**
- Branch: `research/cortex-v1`
- Baseline imutável de origem: `74a1bf9c0f8792a68d7252b11d477835ec93d508`
- Tag baseline publicada: `cortex-pre-research-baseline-20260923`
- Handoff histórico: `docs/HANDOFF-CORTEX.md`
- Runtime: arquitetura herdada ainda controla o jogo; UniversalExecutor Cortex está em SHADOW e não possui nova autoridade live.

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

**F2-E — controlled transactional execution de PreparedStructuralAction.**

F2-D está formalmente concluída.

Evidência:

- implementation commit: 19e0c4fb57f7aa8dbd34c272324383617f7916d1;
- 44 focused PASS;
- 1340 core/FLE PASS;
- 2 PyTorch PASS;
- frontend TypeScript/Vite build PASS;
- live shadow audit: runs/audits/cortex_f2d_live_structural_shadow.json;
- u1778 identificado causalmente como coal mesmo com buffer vazio;
- u1838 desambiguado de coal + iron-ore para iron-ore por ResourceSurvey;
- u1839 identificado como iron-ore;
- branch agregado iron-ore -> iron-plate / stone-furnace;
- PreparedStructuralAction ready=true;
- world_mutation=false;
- scientific runtime F1 continua 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac, dirty=false.

Documento canônico:
docs/CORTEX_PHASE2_STRUCTURAL_PREPARATION.md

Primeiro bloco seguro de F2-E:

1. criar adapter que consome PreparedStructuralAction;
2. exigir ActionAuthority.EXECUTE explicitamente;
3. compilar operações semânticas para FLE usando TransactionalFLEExecutor;
4. medir todas hard postconditions;
5. rollback em postcondition failure;
6. executar primeiro somente em canary/replay isolado;
7. comparar com shadow plan e registrar ActionResult;
8. não conceder continuous autonomous authority.

Não executar seeds confirmatórias 20261101–20261110. Não criar handler específico de green science.

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
