# Cortex Research — Handoff Operacional

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F1-A concluída; F1-B é a próxima subfase autorizada**
- Branch: `research/cortex-v1`
- Baseline imutável de origem: `74a1bf9c0f8792a68d7252b11d477835ec93d508`
- Tag baseline publicada: `cortex-pre-research-baseline-20260923`
- Handoff histórico: `docs/HANDOFF-CORTEX.md`
- Runtime: arquitetura herdada ainda controla o jogo, mas executa somente releases imutáveis por SHA; Cortex continua sem autoridade de controle.

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

**F1-B — baseline corrigida independente por seed: executar 20261002.**

Seed 20261001: **VALID / partial_success**.

- release: 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac, dirty=false;
- run: curriculum-20260924T043932Z;
- completed stages: 14;
- bottleneck: Logistic science;
- autonomy score: 0.5;
- closed-loop: false;
- physical processing coverage: 33.3%;
- manual logistics calls: 53;
- fuel/power starvation final: 0/0;
- isolation global: PASS;
- logistic science output: 0;
- root limitation: producer outputs buffered without processor reach; proposed placement repair
  was not executable because the inherited runner has no_runner_binding_for_intent.

Do not fix gameplay/repair/planning code before finishing the five exploratory seeds. The
baseline runtime remains pinned to 95c34a53, even if analysis/docs commits advance the source
branch.

Next run: seed 20261002, mode exploratory, using the same explicit release root and PYTHONPATH.
Validate result and isolation before 20261003.

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
