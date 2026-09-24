# Cortex Research — Handoff Operacional

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-G1 concluída — F2-G2 autorizada em SHADOW/replay; F3 bloqueada**
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

**F2-G2 — primeira Option temporally extended para estabelecer uma processing chain funcional.**

F2-G1 removeu a dependência Cortex -> `curriculum_runner` para runtime footprints:

- novo instrumento read-only: `factorio_ai_lab.instrumentation.runtime.runtime_entity_footprints`;
- canário Cortex importa o instrumento genérico diretamente;
- `_runtime_entity_footprints` permanece apenas como wrapper de compatibilidade no runner legado;
- falhas de RCON/malformed payload retornam mapa vazio e preservam fallback já existente;
- regressão impede reintroduzir import de `curriculum_runner` no canário.

F2-G1 gates:

- 37 PASS no subset instrumentação/independência;
- 64 PASS no gate combinado continuity/dashboard;
- 1403 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff/compileall/JavaScript/TypeScript/Vite/whitespace PASS.

Implementation commit F2-G1: `f1680811c2bee828c435cbf66c6c03aaf60156e6`.

Documento canônico:

`docs/CORTEX_PHASE2_RUNNER_INDEPENDENCE.md`

F2-F4C continua sendo a última evidência live:

- functional_accept=true;
- processor_output=13;
- transaction_committed=true;
- sustained_operation=false/not proven;
- final processor_status=no_fuel.

A UI F2-G1 deve continuar exibindo esse último canário funcional como evidência histórica, sem
sugerir que F2-G1 executou nova mutação.

F2-G2 deve:

1. definir schema de Option com preconditions/children/effects/termination/provenance;
2. compor structural planning + processor fuel + delivery actuator sem duplicar handlers;
3. modelar option-level time/energy budget com ticks efetivos, não apenas `sleep()`;
4. operar primeiro em SHADOW/replay;
5. manter explicit authority por execução;
6. não usar confirmatory seeds;
7. não conceder continuous autonomous authority.

F2 NÃO está encerrada. Checkboxes originais ainda abertos:

- transactional execution universal;
- options iniciais;
- cadeia funcional sem curriculum_runner;
- runner antigo baseline-only.

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
