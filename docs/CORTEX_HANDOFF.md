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
- F5: ACTIVE / F5-C deterministic baseline · iron_extraction PASS
- F5-C próximo target: coal_self_sufficiency
- Authority Cortex ambiente: A0 / observe only; A2 somente por grant one-shot explícita
- WORLD mutation autorizada em F5-A: não
- Continuous authority: proibida em F5
- Legacy evolution loop: OFF
- Research/curriculum runner: OFF
- WORLD live: conectado; no fechamento F5-A, 0 entities
- Capabilities F5 promovidas: 1 / 9 — iron_extraction
- Intervention ledger: 0
- Commit F5-A: 6f998777a1d8304e5481df8f299d03dc630b0550
- Commit F5-B: 080cd1544da1573cdf21a7c912e7388df45ea383
- Audit F5-B SHA-256: 9bcc4328f13e8dabf250bfe3a2d09ffd16994444571217825491ce703737cc2a

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

A ladder física F5 preregistrada possui 9 capabilities:

1. iron_extraction
2. coal_self_sufficiency
3. iron_smelting
4. steam_power
5. electric_mining
6. copper_chain
7. powered_manufacturing
8. automation_science
9. logistic_science

Após validação read-only do tech tree real do Factorio 2.0, uma emenda
versionada preservou o prefixo já promovido e reordenou somente a cauda
causalmente impossível. A ordem efetiva é:

1. iron_extraction
2. coal_self_sufficiency
3. iron_smelting
4. steam_power
5. copper_chain
6. automation_science
7. powered_manufacturing
8. electric_mining
9. logistic_science

A emenda está em `configs/cortex_f5_capability_order_amendment_v2.json` e
`docs/CORTEX_PHASE5_CAPABILITY_ORDER_AMENDMENT.md`. O manifest/schema v1,
seeds, authority e hard postconditions permanecem congelados. Neste handoff,
as quatro primeiras capabilities já foram promovidas fisicamente; a próxima
frontier efetiva é `copper_chain`.

Promoção exige evidência física + survival invariant. Entity existence, score
offline, memória, loss, UCB, dataset ou inferência não promovem capability.

## Estado do dashboard

A UI atual separa:

1. WORLD LIVE — estado físico RCON;
2. CORTEX CURRENT — F5-A/A0;
3. HISTÓRICO — G97, curriculum, UCB, evolution e learning antigos.

O antigo 6% não é mais progresso corrente. G97 e Online placement learning
podem aparecer apenas como HISTÓRICO/congelado.

## F5-B encerrada

F5-B foi validada em test/shadow, sem mutar Factorio. A bridge reusa PersistentOptionGrantLedger, OptionExecutionBoundary e WorldLease attestation. A2 é estritamente one-shot, expira, é consumida antes da mutação e não pode ser emitida pela learned policy.

## Próxima ação permitida

F5-C — deterministic autonomous baseline.

Objetivo F5-C: usar o stack determinístico para começar a construir fisicamente a capability ladder, concedendo no máximo uma grant A2 por Option, com:

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

F5-C deve iniciar a primeira evidência física F5. A authority ambiente continua A0; cada mutação exige uma nova grant A2 one-shot e WorldLease válida.

## F5-C — primeira capability promovida

- seed development: 245044303
- implementation commit: 7045e6df6fd4afa9770e20b9772665c6e7e08710
- run: cortex-f5c-dev-245044303-20260929T063645Z
- capability: iron_extraction
- physical gate: PASS
- transaction committed: true
- grant A2 one-shot: consumed before mutation
- external resource injection: false
- human intervention count: 0
- next deterministic target: coal_self_sufficiency

As seeds 1619515465 e 853367368 permanecem preservadas como counterexamples de
percepção de recursos e writability do authority ledger, respectivamente.

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
- phase5_checkpoint=F5-B
- phase5_next_checkpoint=F5-C
- phase5_protocol.validated=true
- phase5_protocol.authority_level=A0
- phase5_protocol.world_mutation_authorized=false
- phase5_protocol.artifacts.errors vazio
- phase5_protocol.intervention_ledger.count=0
- resume.do_not_start_another_seed=true

Não habilitar evolution. Não iniciar seed experimental. Não mutar WORLD apenas
para mostrar progresso.
