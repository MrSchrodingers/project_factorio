# Cortex Research — Handoff Operacional

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-F4A concluída em SHADOW — F2-F4B autorizada; novo canário e F3 bloqueados**
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

**F2-F4B — integrar delivery actuator dependency ao one-shot runner, ainda sem canário real.**

F2-F4A está implementada em SHADOW.

Capabilities F2-F4A:

- RuntimeFactorioCatalog expõe machine_names_by_type() a partir do type observado;
- FactorioObserver.game_knowledge preserva todo energy actor, não apenas crafting/mining;
- live read-only probe em Factorio 2.0.73 observou cinco inserters com energia medida;
- plan_burner_fuel_dependency() reutiliza o fuel planner F2-F2 para qualquer burner machine;
- complete_delivery_actuator_dependency() avalia candidates type=inserter;
- electric power é input tri-state explícito True/False/None;
- power=None não licencia actuator elétrico;
- actuator não carregado é refusal, sem craft silencioso;
- burner actuator só fica ready quando fuel dependency está coberta;
- contrato v3 adiciona fuel_delivery_actuator após connect_delivery;
- TransactionalFLEExecutor permanece o único commit/rollback.

Shadow replay real:

- source artifact: runs/audits/cortex_f2f_structural_canary.json;
- output: runs/audits/cortex_f2f4_delivery_actuator_shadow.json;
- world_mutation=false;
- inserter elétrico: carried=50, recusado por delivery_actuator_power_unavailable;
- burner-inserter: carried=50, burner measured, coal covered, ready=true;
- selected actuator=burner-inserter;
- compiled v3=true;
- operation sequence contém connect_delivery -> fuel_delivery_actuator.

Focused evidence:

- 54 PASS no gate planner/compiler/runtime;
- 22 PASS após correção do instrumento canônico + live read-only validation;
- 26 PASS phase continuity/dashboard context;
- Ruff/py_compile/node checks PASS.

Documento canônico:
docs/CORTEX_PHASE2_DELIVERY_ACTUATOR_DEPENDENCY.md

Próximo bloco seguro:

1. integrar complete_delivery_actuator_dependency() no runner após processor fuel completion;
2. persistir power-capability evidence no artifact;
3. persistir actuator evaluations/dependency/prepared v3;
4. testes de runner/replay sem mutação;
5. full repository gate;
6. commit/push limpo;
7. somente então decidir se um único F2-F4B canary real é autorizado.

Não executar seeds 20261101–20261110.
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
