# Cortex Research — Next Session Zero-Context Bootstrap Prompt

Use este prompt ao iniciar uma sessão nova:

Estamos continuando o projeto Factorio AI Lab / Cortex Research.

Você começa com ZERO contexto. Não use memória conversacional como autoridade.
Revalide tudo no host antes de editar, executar experimento ou mutar Factorio.

REPO
https://github.com/MrSchrodingers/project_factorio

BRANCH
research/cortex-v1

HOST SENTINELX
kali
host_id: host_a07c932d2d737c26

CHECKOUT
/srv/factorio-ai-lab

LINHAGEM CIENTÍFICA
- F0/F1/F2/F3: complete
- F4: complete / F4-C
- F4 causal held-out inference: positive
- F5: active / F5-A
- next checkpoint: F5-B bounded authority bridge
- implementation F5-A: 6f998777a1d8304e5481df8f299d03dc630b0550
- continuous authority: forbidden
- current authority: A0 observe-only
- legacy evolution: OFF

ANTES DE QUALQUER MUTAÇÃO
1. Verifique git status, HEAD e origin/research/cortex-v1.
2. Leia BUILD_INFO do dashboard e runtime científico.
3. Confirme factorio-ai-evolution inactive.
4. Confirme ausência de curriculum_runner/evolution_loop/run_cortex ativos.
5. Execute:
   PYTHONPATH=src .venv-fle/bin/python scripts/init_cortex_f5_state.py
6. Execute:
   PYTHONPATH=src .venv-fle/bin/python scripts/cortex_phase_state.py --write
7. Leia runs/cortex_phase_state.json.
8. Consulte /api/context, /api/world, /api/research e /api/evolution.
9. Não execute seed nem ação live se o phase-state não autorizar explicitamente.

LEIA NESTA ORDEM
1. docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md
2. docs/CORTEX_RESEARCH_PROGRAM.md
3. docs/CORTEX_HANDOFF.md
4. docs/CORTEX_PHASE5_AUTONOMY_PROTOCOL.md
5. configs/cortex_f5_autonomy_v1.json
6. configs/cortex_f5_capability_schema_v1.json
7. configs/cortex_f5_trajectory_schema_v1.json
8. configs/cortex_f5_authority_schema_v1.json
9. configs/cortex_f5_intervention_ledger_schema_v1.json
10. docs/CORTEX_DASHBOARD_EVIDENCE_SCOPE.md
11. docs/CORTEX_CONTINUITY_PROTOCOL.md
12. F4 docs apenas para reconstruir causal provenance.

ESTADO ESPERADO, QUE DEVE SER REVALIDADO
- phase=F5
- phase_status=active
- phase4_checkpoint=F4-C
- phase5_checkpoint=F5-A
- phase5_next_checkpoint=F5-B
- F5 protocol validated=true
- authority=A0
- WORLD mutation authorized=false
- continuous authority=false
- evolution OFF
- 9 capabilities, 0 promoted
- intervention ledger count=0
- resume.do_not_start_another_seed=true

F4-C EVIDENCE
- 20/20 valid held-out pairs
- decision=positive
- mean delta_J=0.20221944444444445
- median delta_J=0.001729166666666726
- paired Cohen dz=0.6408858430566287
- exact one-sided p=9.5367431640625e-07
- 95% confidence set=[0.06773986111111112, 0.336780787037037]
- SESOI=0.05
- inference SHA-256=3c4f782dec7c2bb5cebb47a60178ed11b251e2f23618e6341ab514d240499cb7

F5-A EVIDENCE
- implementation commit=6f998777a1d8304e5481df8f299d03dc630b0550
- manifest SHA-256=9ccca2405368d6f74abec6a6beeb2a7e0a514277c47464b362db083ad04147df
- capability/trajectory/authority/intervention schemas frozen
- deterministic partitions frozen: development 8, pilot 8, held-out 12
- F4 confirmatory 20261101–20261110 remains reserved
- dashboard current story is F5-A/A0; G97/UCB/curriculum are historical only
- WORLD at closure was connected and empty (0 entities)

PRÓXIMO TRABALHO
Implementar F5-B bounded authority bridge SEM executar continuous authority.
Primeiro prove o contrato A2 em teste/shadow: uma grant, uma option,
max_executions=1, expiration, exclusive lease, validation, measurement,
commit/rollback e fail-closed. Só depois de código+artefato+testes+commit
considerar um canário live explicitamente bounded.

NÃO:
- reativar evolution;
- reutilizar F4 confirmatory para tuning;
- tratar histórico G97/UCB como processo vivo;
- usar entity existence como capability;
- permitir que learned policy conceda a própria authority;
- iniciar scheduler contínuo;
- mutar WORLD para melhorar a aparência da UI.
