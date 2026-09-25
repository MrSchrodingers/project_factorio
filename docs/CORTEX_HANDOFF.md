# Cortex Research — Handoff Operacional

> **Chat/modelo com zero contexto:** começar por
> `docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md`. Ele contém a fundamentação teórica, roadmap
> F0–F12, fontes de verdade, estado operacional, interpretação do dashboard, protocolo
> SentinelX/GitHub, o fechamento F2-G5 e a próxima fase F3.

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F2 COMPLETE em F2-G5 — F3 READY / NOT STARTED; continuous authority OFF**
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

**F3 — Executive / Cognitive Loop: preparar abertura explícita em SHADOW antes de qualquer nova
authority live.**

F2-G5 fechou o último item do Exit Gate F2:

- implementation commit: 4fc7217d3e5e0fecb076bfd63305e5498eb52f4e;
- canonical audit: runs/audits/cortex_f2g5_baseline_only_enforcement.json;
- audit SHA-256: f7d796d1c406a7425fd3b14a783227364bb62ab42cef477a59960c41f4d721ae;
- run_curriculum exige execution_role=baseline;
- role incorreta recusa antes de gym/FLE/environment/WorldLease;
- launchers legados autorizados se identificam como baseline;
- AST audit confirma zero import de curriculum_runner em src/factorio_ai_lab/cortex;
- world_mutation=false e factorio_rcon_used=false no audit;
- full gate: 1467 core/FLE + 2 PyTorch PASS;
- control-plane freeze commit: 581ed8c38de0a9ffdc908c250063ee9c9a0e8158;
- G4B preflight test isolation: 4ef61eea2d1d6890cba257c5df1a5deaae2c31a0;
- final closure gate após follow-ups: 1467 core/FLE + 2 PyTorch PASS; static/build gates PASS;
- F2 complete mantém resume.do_not_start_another_seed=true;
- Ruff/compileall/JavaScript/TypeScript/Vite/whitespace PASS.

O Exit Gate F2 está mecanicamente validado: API genérica de Options, boundary transacional
universal, cadeia funcional live sem curriculum_runner e runner legado baseline-only.

A última evidência live continua sendo F2-G4B, run cortex-f2g4b-20260925T014418Z, com 13 iron
plates e final no_fuel. Não repetir esse canário. Sustentabilidade autônoma continua não provada.

Dashboard pós-fechamento:

- hardening base no commit F2-G5: stale RCON, bootstrap parcial e WebSocket resiliente;
- hotfix world-without-avatar: 95254cc1b81cc75a90debf6ab93a01ddd0099485;
- observers usam agent surface/force quando disponível e fallback read-only para
  game.surfaces[1] + game.forces.player quando o avatar não existe;
- gate do hotfix: 1469 core/FLE + 2 PyTorch PASS; static/build gates PASS;
- validação live: 17/17 endpoints de bootstrap HTTP 200, WebSocket sem stream_error;
- http://midasnet.tail106aa2.ts.net:8765/, /api/context e /api/world: HTTP 200;
- /api/world: connected=true, observer_origin=world_fallback, entity_count=0;
- /api/resource-overview: connected=true, 38 cells e 2562 resource points;
- o zero de factory entities é estado físico observado da force player, não falha de conexão;
- /api/context: global / Cortex, F2 complete, checkpoint F2-G5, Exit Gate validado.

Não executar confirmatory seeds.
Não habilitar evolution.
Não conceder continuous autonomous authority.
F3 está liberada pelo Exit Gate de F2, mas ainda não foi iniciada.

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
