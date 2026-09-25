# Cortex Research — Handoff Operacional

> **Chat/modelo com zero contexto:** começar por
> `docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md`. Ele contém a fundamentação teórica, roadmap
> F0–F12, fontes de verdade, estado operacional, interpretação do dashboard, protocolo
> SentinelX/GitHub, o fechamento F2-G5 e a próxima fase F3.

**Documento de continuidade curta.** O contrato completo está em
`docs/CORTEX_RESEARCH_PROGRAM.md`.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Fase: **F4 ACTIVE em F4-A / SHADOW — F3 COMPLETE; continuous authority OFF**
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

**F4-B — hybrid structural/similarity retrieval + consolidation + decay policy.**

F4-A está validada em SHADOW:

- implementation: 756702bb6961fe8c989b2658b0a020200b7632bf;
- canonical artifact: runs/audits/cortex_f4a_memory_substrate_migration.json;
- artifact SHA-256: f45e31785c17cd6222a57937564036dbdd4976ee1d6376b61f340a9d70066228;
- persistent store: runs/ledger/cortex_cognitive_memory.sqlite3;
- canonical batch: cortex-f4a-af2a612bb1327f0b2fb2;
- batch occurrences: 759;
- batch manifest SHA-256:
  cfe4472fc53d8f20f9ee4bf3e16951d8689d3bd7d381e7c5d663ae4f61637e71;
- durable memory items: 222;
- episodic: 54 items / 54 occurrences;
- semantic: 134 items / 566 occurrences / 530 qualified;
- procedural: 1 item / 54 executed supports;
- counterexample: 33 items / 85 occurrences;
- procedure mean reward: 0.8333333333333334;
- procedure Wilson lower-95: 0.7126323220121027;
- working memory bounded capacity=3 and non-persistent;
- memory DB quick_check=ok;
- authority=shadow;
- world_mutation=false;
- FLE/RCON/WorldLease/execution grant unused;
- full gate: 1508 core/FLE + 2 PyTorch PASS plus static/build gates.

F4-A is a memory substrate result, not a causal-memory result. It does not prove retrieval quality or
that memory changes outcomes.

F4-B must move Cortex recall out of the legacy stage/recency-only JSONL path and provide explicit
structural + similarity ranking, provenance/support-aware retrieval, consolidation and a defensible
decay/forgetting policy. Preserve original occurrences; do not destructively erase evidence.

F4 Exit Gate remains an ablation/transfer criterion and is not satisfied by F4-A.

Ainda não executar confirmatory seeds.
Não habilitar evolution.
Não conceder continuous autonomous authority.
Não iniciar live Cortex execution para fechar F4-B.

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
