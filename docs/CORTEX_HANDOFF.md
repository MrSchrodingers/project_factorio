# Cortex Research — Handoff Operacional

> **Chat/modelo com zero contexto:** começar por
> `docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md`. Ele contém a fundamentação teórica, roadmap
> F0–F12, fontes de verdade, estado operacional, interpretação do dashboard, protocolo
> SentinelX/GitHub e o plano autorizado F2-G4A.

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-G3 concluída — F2-G4A autorizada para authority ledger persistente; F3 bloqueada**
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

**F2-G4A — persistent one-shot Option authority ledger + dry-run integration.**

F2-G3 fechou o boundary universal de execução da primeira Option:

- `ProcessingChainOptionPlan -> OptionExecutionBoundary -> StructuralTransactionalAdapter -> TransactionalFLEExecutor`;
- digest SHA-256 do plano congelado;
- grant ligado a option/prepared action/digest/SHA/run;
- lineage Option -> ActionRequest -> Branch -> Prepared validado antes de authority;
- termination funcional permanece reaching_processor + processor_exists + processor_output;
- SHADOW/PROPOSAL não chamam runtime;
- EXECUTE exige executor, measurement probe e tick source observável antes da mutação;
- accepted fake transaction devolveu 600 observed ticks ao OptionBudget;
- rejected fake transaction com rollback devolveu `observed_ticks=null` + `missing_after_rollback`;
- nenhum import/dispatch de `curriculum_runner`;
- `continuous_authority=false`.

Gates F2-G3:

- 26 PASS boundary/composer/transaction focused;
- 53 PASS integrated continuity/dashboard;
- full core/FLE: 1421 PASS;
- PyTorch: 2 PASS;
- Ruff/compileall/JavaScript/TypeScript/Vite/whitespace: PASS.

Implementation commit:

`e25569db40d6e6186cc24b3c380ebdc9dc4e84cf`

Artifact fake/replay:

`runs/audits/cortex_f2g3_option_execution_fake.json`

SHA-256:

`3a2cdf621e2592766cdec3f5a83ca057b42f3b3c184ce356437a1795a4b97f65`

Limitação crítica:

- o consumo de grant é process-local;
- reiniciar/recriar o boundary perde o estado de consumo;
- portanto isso NÃO é durable live one-shot authority;
- nenhum live Option EXECUTE foi executado em F2-G3;
- último EXECUTE live continua sendo F2-F4C;
- sustentabilidade live continua não provada.

Documento canônico:

`docs/CORTEX_PHASE2_OPTION_EXECUTION_BOUNDARY.md`

F2-G4A deve:

1. criar ledger persistente de grants;
2. consumir grant atomicamente antes da mutação;
3. recusar grant consumido/stale após restart;
4. adicionar expiry/scope explícitos;
5. manter continuous authority OFF;
6. integrar um runner de Option independente de `curriculum_runner`, ainda em dry-run;
7. não tocar seeds confirmatórias;
8. manter evolution inactive+disabled.

F2 NÃO está encerrada.

Status dos requisitos originais:

- initial Option: atendido em F2-G2;
- transactional execution universal: boundary existe, mas durable live authority ainda aberto;
- cadeia funcional por Option/API genérica: validada em fake/replay, ainda não em Factorio live;
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
