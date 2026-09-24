# Cortex Research — Handoff Operacional

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2-F4B full gate PASS — implementação/UI publicadas; verificação pós-publicação pendente; F3 bloqueada**
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

**F2-F4C — revalidar isolamento e, somente se todos os gates permanecerem verdes, executar exatamente um canário não-confirmatório.**

F2-F4B está concluída.

Publicação:

- runner integration: `0607d1286b3a0c885e03c11c23e51935e2a16aa1`;
- dashboard state: `beb96a6a5fd7142cb6d6d1a3bfaa48d380f9ed65`;
- documentação de fechamento: commit posterior a estes dois.

Evidence final:

- 60 focused PASS;
- 76 continuity/dashboard PASS;
- 1400 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff/compileall/TypeScript/Vite PASS;
- dry-run artifact `runs/audits/cortex_f2f4b_dryrun.json`, `world_mutation=false`;
- replay `runs/audits/cortex_f2f4b_runner_replay.json`, `world_mutation=false`;
- replay selecionou `burner-inserter` + coal e compilou contract v3.

Power contract:

- zero power edges + fixture sem power operation => `derived_unavailable`;
- zero edges sem fixture contract => unknown;
- rede existente sem medição posicional => unknown;
- missing nunca vira false/zero por default.

Antes de F2-F4C:

1. confirmar HEAD local == origin/research/cortex-v1;
2. árvore Git limpa;
3. evolution inactive+disabled;
4. runtime científico F1 intacto em `95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac`;
5. confirmar seed 424242 fora do holdout;
6. confirmar inexistência do artifact F2-F4C;
7. executar exatamente uma vez;
8. não repetir automaticamente se alcançar a capability e resultar em reject;
9. classificar o resultado antes de qualquer nova alteração.

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
