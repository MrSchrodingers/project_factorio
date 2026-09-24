# Cortex Research — Handoff Operacional

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-F4C functional accept concluído — F2-G autorizada; F3 bloqueada**
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

**F2-G — Option composition + runner independence.**

F2-F4C terminou com functional accept real em Factorio.

Evidence principal:

- source commit: `20aac7f8eb0c3b71c8017b632892f37624b79fd0`;
- seed 424242;
- artifact: `runs/audits/cortex_f2f4c_structural_canary.json`;
- SHA-256: `2c074e8ec312a5119c59487ee25acae5f1cab7f2ca7e2a2c95cedffada955d37`;
- action_status=accepted;
- transaction_committed=true;
- rollback_observed=false;
- producers_reaching_processor: 0 -> 1;
- physical_processing_coverage: 0.0 -> 1.0;
- processor_output: 0.0 -> 13.0;
- all hard postconditions satisfied;
- actuator selecionado=burner-inserter;
- actuator fuel=coal;
- processor fuel=coal;
- continuous_authority=false.

Limitação científica:

- final processor_status=no_fuel;
- portanto functional_accept=true, mas sustained_operation=false/not proven;
- F2-F4C prova operação funcional bounded, não produção sustentável contínua.

Diagnóstico temporal fechado:

- 1 coal no stone-furnace = 44,44 s de jogo;
- iron plate = 3,2 s; 44,44 s comportam exatamente 13 crafts completos;
- F2-F4C observou 13 plates + no_fuel;
- FLE roda game.speed=10 e só pausa ao final de `FactorioGymEnv.step()`;
- pós-eval (GameState/verification/observation) continua consumindo ticks;
- `settle_seconds=10` não representa o horizon energético total da transação;
- F2-G deve instrumentar tick horizon efetivo e não aumentar coal por constante arbitrária.

Artifact: `runs/audits/cortex_f2f4c_temporal_diagnosis.json`.

Segurança:

- confirmatory seeds 20261101–20261110 seguem intactas;
- runtime F1 continua 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac;
- evolution permanece inactive+disabled;
- FactorioWorldLease bloqueou uma tentativa concorrente duplicada antes de qualquer mutação.

Documento canônico:
`docs/CORTEX_PHASE2_DELIVERY_ACTUATOR_CANARY.md`

Closure gate:

- 41 focused continuity/dashboard PASS;
- 1401 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff/compileall/JavaScript/TypeScript/Vite PASS;
- mechanical state/UI commit: `01c4390d1668631722dcef8d33c980e7e704247e`.

F2 NÃO está encerrada.

Checklist original ainda aberto:

- transactional execution universal;
- options iniciais;
- cadeia funcional sem curriculum_runner;
- runner antigo baseline-only.

Próximo bloco seguro F2-G:

1. remover dependência Cortex -> curriculum_runner para runtime footprints;
2. mover essa instrumentação para interface genérica de planning/runtime;
3. formalizar uma option temporally extended para estabelecer processing chain funcional;
4. compor essa option por primitives/typed dependencies já existentes;
5. executá-la pelo boundary transacional genérico, sem stage handler;
6. adicionar integration test sem import de curriculum_runner;
7. só então avaliar os quatro checkboxes de fechamento da F2;
8. manter continuous autonomous authority bloqueada.

Não executar confirmatory seeds.
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
