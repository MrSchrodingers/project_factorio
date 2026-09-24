# Cortex Research — Handoff Operacional

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-G2 concluída — F2-G3 autorizada em SHADOW/replay; F3 bloqueada**
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

**F2-G3 — universal Option execution boundary, primeiro em SHADOW/replay.**

F2-G2 formalizou a primeira Option temporally extended do Cortex:

- kind: `establish_processing_chain`;
- initiation = preconditions do branch estrutural;
- children = structural planner -> prepare v1 -> processor energy v2 -> delivery actuator v3;
- termination = reaching_processor + processor_exists + processor_output;
- authority F2-G2 = SHADOW/PROPOSAL; EXECUTE recusado;
- provenance explícita Option -> ActionRequest -> ProcessingBranch;
- ambiguity entre múltiplos materiais é refusal, não escolha silenciosa;
- requested ticks não viram observação;
- observed game ticks, quando disponíveis, ampliam o horizon de energy planning;
- `runtime_game_ticks()` está em instrumentation genérica; runner legado só mantém wrapper.

Gates F2-G2:

- focused composer/replay: 48 PASS;
- full core/FLE: 1413 PASS;
- PyTorch: 2 PASS;
- Ruff/compileall/JavaScript/TypeScript/Vite/whitespace: PASS.

Implementation commit:

`9c57b7b1fa8b804113d77044df8cf0c3feba4355`

Replay canônico:

`runs/audits/cortex_f2g2_option_contract_replay.json`

SHA-256:

`b05b20cc0070dcb16180757b927702e790947bb686913522af30499a20a59d21`

Limitação do replay histórico:

- F2-F4C preservou labels dos instrumentos, mas não os payloads crus world/catalog/resources/inventory;
- portanto `planner_reexecuted=false` e `historical_inputs_replayable=false`;
- full planner replay NÃO é reivindicado;
- source output=13 e final no_fuel permanecem apenas como evidência histórica F2-F4C;
- sustainability_evaluable=false porque observed ticks do ciclo completo não foram persistidos.

Documento canônico:

`docs/CORTEX_PHASE2_OPTIONS.md`

F2-G3 deve:

1. receber um `ProcessingChainOptionPlan` por API tipada;
2. reutilizar `StructuralTransactionalAdapter` / `TransactionalFLEExecutor`;
3. manter um único rollback abaixo do Cortex;
4. medir `runtime_game_ticks()` before/after e devolver ticks observados ao OptionBudget;
5. preservar lineage Option -> Action -> transaction;
6. executar primeiro em fake/replay transacional, sem world live;
7. provar integração funcional sem import/dispatch de `curriculum_runner`;
8. manter termination funcional inalterada;
9. só considerar one-shot EXECUTE live em checkpoint posterior.

F2 NÃO está encerrada.

Status dos requisitos originais:

- initial Option: atendido em F2-G2;
- transactional execution universal: ainda aberto;
- cadeia funcional por Option/API genérica: ainda não executada;
- runner legado baseline-only: ainda aberto.

Não executar confirmatory seeds.
Não conceder continuous autonomous authority.
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
