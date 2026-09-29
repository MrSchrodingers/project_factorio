# Cortex Research — Handoff Operacional

Chat/modelo com zero contexto: começar por docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md,
depois ler este arquivo, docs/CORTEX_PHASE5_AUTONOMY_PROTOCOL.md e regenerar
runs/cortex_phase_state.json. Não usar memória conversacional como autoridade operacional.

## Estado atual

- Programa: Cortex Research Architecture v0.1
- Branch: research/cortex-v1
- F0/F1/F2/F3: concluídas
- F4: COMPLETE / F4-C
- F4 causal memory inference: positive
- F5: ACTIVE / F5-A protocol freeze
- Próximo checkpoint: F5-B — bounded authority bridge
- Authority Cortex atual: A0 / observe only
- WORLD mutation autorizada em F5-A: não
- Continuous authority: proibida em F5
- Legacy evolution loop: OFF
- Research/curriculum runner: OFF
- WORLD live: conectado; no fechamento F5-A, 0 entities
- Capabilities F5 promovidas: 0 / 9
- Intervention ledger: 0
- Commit de implementação F5-A: 6f998777a1d8304e5481df8f299d03dc630b0550
- Dashboard release F5-A: 6f998777a1d8304e5481df8f299d03dc630b0550

## F4-C encerrada

A inferência primária preregistrada da F4-C fechou com 20/20 pares válidos:

- decision: positive
- mean delta_J: 0.20221944444444445
- median delta_J: 0.001729166666666726
- paired Cohen dz: 0.6408858430566287
- exact one-sided p: 9.5367431640625e-07
- 95% confidence set: [0.06773986111111112, 0.336780787037037]
- SESOI delta_J: 0.05
- inference artifact SHA-256: 3c4f782dec7c2bb5cebb47a60178ed11b251e2f23618e6341ab514d240499cb7

Pilot não entra na inferência primária. Confirmatory F4 20261101–20261110
permanece congelada e não deve ser reutilizada para tuning.

## F5-A encerrada

Artefatos canônicos:

- docs/CORTEX_PHASE5_AUTONOMY_PROTOCOL.md
- configs/cortex_f5_autonomy_v1.json
- configs/cortex_f5_capability_schema_v1.json
- configs/cortex_f5_trajectory_schema_v1.json
- configs/cortex_f5_authority_schema_v1.json
- configs/cortex_f5_intervention_ledger_schema_v1.json
- scripts/init_cortex_f5_state.py
- runtime ledger: runs/cortex_f5_intervention_ledger.json
- F5 manifest SHA-256: 9ccca2405368d6f74abec6a6beeb2a7e0a514277c47464b362db083ad04147df

Seeds F5 são preregistradas em três partitions independentes:

- development: 8
- pilot: 8
- held-out: 12
- F4 confirmatory reserved: 10, separada e não reutilizável

A ladder física F5 possui 9 capabilities:

1. iron_extraction
2. coal_self_sufficiency
3. iron_smelting
4. steam_power
5. electric_mining
6. copper_chain
7. powered_manufacturing
8. automation_science
9. logistic_science

Promoção exige evidência física + survival invariant. Entity existence, score
offline, memória, loss, UCB, dataset ou inferência não promovem capability.

## Estado do dashboard

A UI atual separa:

1. WORLD LIVE — estado físico RCON;
2. CORTEX CURRENT — F5-A/A0;
3. HISTÓRICO — G97, curriculum, UCB, evolution e learning antigos.

O antigo 6% não é mais progresso corrente. G97 e Online placement learning
podem aparecer apenas como HISTÓRICO/congelado.

## Próxima ação permitida

F5-B — bounded authority bridge.

Objetivo: provar que o Cortex pode receber uma grant A2 para exatamente uma
option transacional, com:

- scope explícito;
- max_executions=1;
- expiração;
- lease exclusiva;
- validate antes de execute;
- measure depois de execute;
- commit/rollback;
- fail-closed;
- nenhuma auto-elevação de authority pela policy;
- nenhum scheduler contínuo.

Enquanto F5-B não estiver validada, manter A0 e não mutar o WORLD.

## Regra de retomada

1. git status --short --branch
2. confirmar HEAD/origin de research/cortex-v1
3. confirmar dashboard BUILD_INFO
4. confirmar evolution inactive
5. confirmar ausência de runners Cortex/curriculum/evolution
6. inicializar ledger idempotentemente:
   PYTHONPATH=src .venv-fle/bin/python scripts/init_cortex_f5_state.py
7. regenerar phase state:
   PYTHONPATH=src .venv-fle/bin/python scripts/cortex_phase_state.py --write
8. ler runs/cortex_phase_state.json
9. consultar /api/context, /api/world, /api/research, /api/evolution
10. obedecer resume.do_not_start_another_seed

Estado esperado:

- phase=F5
- phase_status=active
- phase4_checkpoint=F4-C
- phase5_checkpoint=F5-A
- phase5_next_checkpoint=F5-B
- phase5_protocol.validated=true
- phase5_protocol.authority_level=A0
- phase5_protocol.world_mutation_authorized=false
- phase5_protocol.artifacts.errors vazio
- phase5_protocol.intervention_ledger.count=0
- resume.do_not_start_another_seed=true

Não habilitar evolution. Não iniciar seed experimental. Não mutar WORLD apenas
para mostrar progresso.
