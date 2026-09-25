# Cortex Research — Handoff Operacional

> **Chat/modelo com zero contexto:** começar por
> `docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md`. Ele contém a fundamentação teórica, roadmap
> F0–F12, fontes de verdade, estado operacional, interpretação do dashboard, protocolo
> SentinelX/GitHub e o plano autorizado F2-G4A.

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-G4A concluída — F2-G4B próximo checkpoint; F2 ativa; F3 bloqueada**
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

**F2-G4B — um único live Option canary não-confirmatório, somente após publicação/deploy de G4A.**

F2-G4A fechou a limitação process-local de F2-G3:

- implementation commit: f567bf453c9e3c0e8dfb319adfeef266b4926af8;
- ledger SQLite persistente com schema versionado;
- grant_id + option/prepared/digest/SHA/run + issued/expiry + scope;
- run_id obrigatório;
- scope exato, sem wildcard, max_executions=1;
- duplicate grant id não é upsert;
- BEGIN IMMEDIATE + synchronous=FULL;
- consumo persistido antes de qualquer runtime mutation;
- grant consumido continua recusado após reconstruction/restart;
- crash após consume e antes da mutação permanece fail-closed;
- double-consume concorrente testado também com processos spawned;
- SHADOW/PROPOSAL continuam sem runtime;
- EXECUTE sem ledger persistente é recusado;
- nenhum scheduler ou continuous grant.

Dry-run oficial:

- artifact: runs/audits/cortex_f2g4a_option_authority_dry_run.json;
- SHA-256: 94b60b7b8a298252edcb37b4435854c97f83e0f6832de9ec46aec05aff1e1ec6;
- run: cortex-f2g4a-dry-20260925T011524Z;
- code revision: f567bf453c9e3c0e8dfb319adfeef266b4926af8, dirty=false;
- status=pass;
- factorio_environment_created=false;
- factorio_rcon_used=false;
- factorio_world_mutation=false;
- continuous_authority=false;
- live_option_execute_authorized=false;
- exact grant valid;
- scope-mismatch negative control refused;
- consumed_at=null / consume_result=null.

Gates F2-G4A:

- full core/FLE: 1440 PASS;
- PyTorch: 2 PASS;
- Ruff/compileall/JavaScript/TypeScript/Vite/whitespace: PASS;
- phase-state não promove G4A apenas pela existência do documento; exige artifact válido e
  explicitamente não-mutante.

Documento canônico:

docs/CORTEX_PHASE2_PERSISTENT_OPTION_AUTHORITY.md

F2 NÃO está encerrada.

Ainda abertos:

- live functional chain através de Option/API genérica;
- universal transactional execution demonstrada em live sob grant durável;
- enforcement formal do runner legado como baseline-only;
- sustained autonomous operation.

F2-G4B deve usar exatamente um grant persistente, scope ligado ao experimento/WorldLease real,
nenhum retry automático, hard functional termination inalterado, commit/rollback transacional,
tick evidence e continuous_authority=false.

Não executar confirmatory seeds.
Não habilitar evolution.
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
