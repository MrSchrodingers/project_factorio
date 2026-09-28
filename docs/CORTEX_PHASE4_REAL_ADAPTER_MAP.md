# Cortex F4-C — mapa dos adapters reais

Status: **four adapters implemented; disposable NON-PROTOCOL readiness audit pending**.

Este documento liga as quatro famílias congeladas do protocolo F4-C aos
componentes existentes do projeto. Ele não é evidência experimental e não
altera `execution_ready`.

## Invariantes comuns

Todo adapter real deve implementar o contrato de `PairedWorldAdapter` de
`src/factorio_ai_lab/cortex/causal_harness.py` e operar somente dentro do
harness pareado:

- capturar um único checkpoint antes do par;
- restaurar exatamente o mesmo checkpoint antes de MEMORY ON e MEMORY ABLATED;
- receber o mesmo task spec, candidate surface, tool surface e budget;
- permitir que a condição mude apenas o resultado de retrieval;
- usar `MemoryAccess.quarantine_write()` para qualquer escrita produzida pelo
  turno;
- devolver todos os componentes primários necessários para J;
- nunca converter missing em zero;
- nunca reutilizar o estado final de um braço no outro;
- invalidar tecnicamente o par se restore, medição ou outcome extraction falhar.

O primeiro teste real deve usar um mundo descartável **NON-PROTOCOL**. Nenhuma
seed pilot/evaluation/confirmatory pode ser usada para validar adapters.

## 1. structural_flow_repair

**Binding existente**

- `factorio_ai_lab.cortex.structural_prepare.prepare_structural_branch`
- `factorio_ai_lab.cortex.structural_execute.StructuralTransactionalAdapter`
- `factorio_ai_lab.cortex.structural_execute.compile_structural_action`
- contratos/postconditions já usados pelo caminho estrutural controlado.

**Responsabilidade do adapter F4-C**

Traduzir o task spec congelado para branches/opções estruturais tipadas,
preservar a mesma candidate surface nos dois braços, executar transacionalmente
e projetar as postconditions F4-C:

- `processor_exists`
- `producer_reaches_processor`
- `processor_output_increases`
- `no_new_dead_end`

Não criar uma nova linguagem de ação estrutural.

## 2. fuel_energy_recovery

**Binding existente**

- `factorio_ai_lab.learning.repair_loop.detect_deficits`
- `diagnose`
- `propose_actions`
- `select_action`
- `order_steps`
- `plan_repairs`
- planners de resupply existentes em `factorio_ai_lab.planning.resupply`.

**Responsabilidade do adapter F4-C**

Construir `RepairObservation` a partir do mesmo snapshot físico para os dois
braços e permitir que memória influencie apenas a seleção/ranking quando o
protocolo autorizar. O executor/repair surface permanece idêntico.

Postconditions:

- `energy_dependency_resolved`
- `target_chain_resumes`
- `bootstrap_dependency_not_increased`
- `no_validated_capability_regresses`

## 3. spatial_logistics_routing

**Binding existente**

- `factorio_ai_lab.planning.astar.weighted_astar`
- `RoutingWeights`
- `rectangular_bounds`
- `blocked_from`

Esse é o adapter mais adequado para o primeiro preflight real por possuir
solver determinístico pequeno e outcome diretamente observável.

**Responsabilidade do adapter F4-C**

Materializar grid, start, goal e obstáculos do task spec em `GridPoint`,
executar a mesma superfície de candidatos em ambos os braços e medir:

- `route_found`
- `route_collision_free`
- `endpoints_connected`
- `route_within_resource_budget`

Nenhum resultado do solver pode ser usado como label de solução na geração da
task.

## 4. production_transition_planning

**Binding existente**

- `factorio_ai_lab.planning.production_dag.ProductionDagPlanner`
- `factorio_ai_lab.planning.dependency_plan`
- runtime/factorio catalogs já usados pelo Production DAG.

**Responsabilidade do adapter F4-C**

Construir o DAG a partir do mesmo catálogo/runtime em ambos os braços, produzir
uma ordem de dependências e validar fisicamente ou em preflight descartável:

- `required_material_budget_satisfied`
- `dependency_order_valid`
- `target_stage_functional`
- `no_validated_capability_regresses`

## Ordem de implementação / validação

1. `spatial_logistics_routing`: validar binding, budgets, restore e outcome
   extractor com solver determinístico.
2. `fuel_energy_recovery`: validar observação → diagnóstico → repair surface.
3. `structural_flow_repair`: reutilizar o adapter transacional já existente.
4. `production_transition_planning`: fechar DAG + material budget + outcome
   físico.

Depois dos quatro adapters passarem no mundo descartável NON-PROTOCOL, gerar
um audit separado de adapter readiness. Somente esse audit pode habilitar
`real_task_adapters_validated=true`; o preflight sintético isoladamente nunca
habilita `execution_ready`.

## Critério de não-regressão

Mesmo após adapters reais passarem:

- evolution continua OFF até autorização científica separada;
- continuous authority continua false;
- confirmatory `20261101–20261110` permanecem congeladas;
- pilot `20261201–20261208` só pode começar quando o phase-state reportar
  `execution_ready=true`;
- evaluation `20261221–20261240` permanece intocada durante tuning/preflight.


## Implementação atual

Os quatro bindings deste mapa agora existem em
src/factorio_ai_lab/cortex/causal_real_adapters.py:

- SpatialRoutingPairedAdapter usa weighted_astar;
- FuelRecoveryPairedAdapter usa repair_loop.plan_repairs e
  resupply.plan_supply;
- StructuralFlowPairedAdapter usa o stack transacional existente
  prepare_structural_branch -> compile_structural_action ->
  StructuralTransactionalAdapter -> TransactionalFLEExecutor sobre um
  ambiente descartável checkpointado;
- ProductionTransitionPairedAdapter usa ProductionDagPlanner e
  DependencyPlanner sobre um catálogo runtime determinístico descartável.

A implementação isolada não habilita execution_ready. O gate é o artefato
runs/audits/cortex_f4c_real_adapters_validation.json, gerado apenas por
scripts/validate_cortex_f4c_real_adapters.py a partir de árvore Git limpa.
Esse audit deve provar simultaneamente os quatro bindings, restore pareado,
ablação retrieval-only, budgets/surfaces casados, escrita em quarentena,
memória fonte imutável e firewall contra qualquer partition/seed protocolar.

Mesmo com o audit PASS, o significado é somente pilot-ready. Nenhum resultado
causal foi observado; o F4 Exit Gate continua aberto.
