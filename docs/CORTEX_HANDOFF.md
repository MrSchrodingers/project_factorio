# Cortex Research — Handoff Operacional

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-F3 concluída como counterexample válido — F2-F4 autorizada; F3 bloqueada**
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

**F2-F4 — delivery actuator dependency / energy-aware delivery.**

F2-F3 já produziu um experimento válido em Factorio real no commit
e1aad03dafa8604a002ca11641f0000b69cbefa0, seed 424242.

Resultado causal:

- functional_dependency.ready = true;
- fuel selecionado = coal;
- fuel units = 1;
- carried_only = true;
- operação fuel_processor presente;
- producers_reaching_processor: 0 -> 1;
- physical_processing_coverage: 0.0 -> 1.0;
- processor_exists: false -> true;
- processor_status: no_ingredients;
- processor_output: 0.0;
- ActionResult = rejected;
- structural_postcondition_failed;
- transaction_committed = false;
- rollback_observed = true.

Interpretação:

F2-F2 resolveu a dependência de combustível do stone-furnace: o processor deixou de
falhar por no_fuel. A nova falha é de alimentação de material.

A inspeção do contrato mostra:

- connect_delivery usa entities=["inserter"];
- structural_prepare hardcodeia o inserter elétrico;
- planning/delivery.py não modela power/energy;
- o canário não cria uma rede elétrica;
- burner-inserter também estava disponível no inventory.

Portanto F2-F4 deve modelar a dependência funcional do actuator de delivery, não relaxar
processor_output e não inserir um special-case de iron/green science.

Artifact científico válido:
runs/audits/cortex_f2f_structural_canary.json

Uma execução posterior gravada em
runs/audits/cortex_f2f3_structural_canary_attempt1_invalid_bootstrap.json
falhou antes da capability por timing do fixture producer+buffer. Ela foi classificada como
invalid experiment / fixture failure e não substitui o F2-F3 válido.

O fixture foi endurecido com polling bounded 1 s / deadline 12 s e telemetria explícita.
Hardening publicado em `0a747b99328bf084a82fdde79ee8259018ba7931` — `fix: estabiliza fixture do canário Cortex`.

Próximo bloco seguro:

1. validar/deployar o dashboard F2-F3 e publicar a tag de fechamento;
2. criar F2-F4 como planner puro de delivery actuator dependency;
3. resolver actuator por energia observada/capability disponível, nunca por nome hardcoded;
4. reutilizar MachineEnergy/runtime catalog e planners existentes;
5. para actuator burner, compor fuel dependency tipada;
6. para actuator elétrico, exigir power capability observada ou child option explícita;
7. manter TransactionalFLEExecutor como único commit/rollback;
8. manter processor_output INCREASE como hard gate;
9. validar em shadow/replay antes de qualquer novo canário real.

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
