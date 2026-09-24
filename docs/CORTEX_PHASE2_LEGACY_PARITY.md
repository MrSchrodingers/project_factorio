# Cortex Research — F2-B Legacy Parity Adapters

**Phase:** F2 — Action/Option Ontology e Universal Executor
**Checkpoint:** F2-B — parity adapters
**Authority:** SHADOW / PREPARE ONLY
**Implementation commit:** 7c38cd7c9f0f67068e449fa3e5ff107b74ebcbd1
**Baseline runtime:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac

## 1. Objetivo

F2-A criou a ontology universal. F2-B verifica se essa ontology consegue preparar exatamente a mesma operação que handlers legados já validados executariam, sem ainda conceder authority EXECUTE.

O princípio é:

    typed request -> prepared legacy-compatible operation

e NÃO:

    typed request -> world mutation

A separação é deliberada. Antes de permitir que UniversalExecutor execute qualquer ação, o sistema deve demonstrar parity de script, purpose, targets e refusal semantics.

## 2. Escopo

Este checkpoint cobre dois handlers herdados de menor risco:

- resupply:insert_fuel_from_world_container;
- placement:extend_power_supply_to_machine / attach_machine_to_live_network.

Eles correspondem a:

- curriculum_runner._repair_insert_fuel;
- curriculum_runner._repair_power_tap.

Não altera curriculum_runner.py.

## 3. PreparedLegacyAction

PreparedLegacyAction contém:

- action_id;
- family;
- intent;
- binding;
- legacy_handler;
- code;
- purpose;
- measurement_keys;
- preflight.

Ele é uma descrição executável compatível com o legado, porém não contém método de execução e não toca o mundo.

## 4. PreparationResult

PreparationResult possui exatamente um de:

- prepared;
- refusal.

Isso impede um estado ambíguo em que uma ação apareça ao mesmo tempo pronta e recusada.

## 5. Resupply parity

Para insert_fuel_from_world_container, o adapter usa os mesmos helpers do runner:

- repair_entity_targets;
- _carried_item_count;
- repair_fuel_sources;
- repair_refuel_code;
- REPAIR_FUEL_DOSE.

Preserva recusas:

- action_named_no_repairable_entity;
- world_and_agent_hold_no_fuel.

Preserva intervention purpose:

    operation

Preserva measurement contract:

- inserted;
- drawn;
- note.

## 6. Power-tap parity

Para extend_power_supply_to_machine e attach_machine_to_live_network, o adapter usa:

- repair_entity_targets;
- repair_power_tap_code.

Preserva refusal:

- action_named_no_repairable_entity.

Preserva intervention purpose:

    infrastructure

Preserva measurement contract:

- poles;
- pole_stock;
- note.

## 7. Teste de equivalência

tests/test_cortex_legacy_parity.py executa duas trilhas sobre o mesmo mundo sintético.

Trilha Cortex:

    RepairAction
    -> ActionRequest
    -> LegacyRepairParityAdapter.prepare()
    -> PreparedLegacyAction

Trilha legada:

    RepairAction
    -> _repair_insert_fuel / _repair_power_tap
    -> CaptureExecutor

O teste compara diretamente:

- code preparado == code entregue pelo handler legado;
- purpose preparado == purpose entregue ao TransactionalFLEExecutor;
- measurement keys esperadas;
- refusal codes;
- target resolution.

A equivalência é sobre comportamento de preparação, não somente sobre nomes de classes.

## 8. Resultado

Focused parity/regression gate:

    69 passed

Inclui:

- F2 action ontology;
- legacy parity adapters;
- stage repair;
- repair loop;
- TransactionalFLEExecutor.

Full regression gate:

    1323 core/FLE passed
    2 PyTorch passed
    static checks passed

## 9. Counterexample estrutural preservado

O intent:

    placement:place_processing_for_buffered_output

continua deliberadamente sem adapter F2-B.

O teste exige:

    refusal = no_runner_binding_for_intent

Isso evita mascarar o problema central da F1.

F2-C deve resolver esse gap como nova capacidade, não fazer o adapter fingir que o handler legado já existe.

## 10. Autoridade

F2-B continua sem nova live authority.

O adapter prepara código, mas não chama TransactionalFLEExecutor.

UniversalExecutor permanece em SHADOW para esses bindings.

O checkbox "transactional execution universal" continua aberto.

## 11. Relação com segurança

F2-B preserva:

- missing != zero;
- no implicit authority;
- named refusals;
- provenance;
- purpose accounting;
- baseline runtime imutável;
- confirmatory seeds não executadas.

## 12. Próximo checkpoint — F2-C

F2-C implementará o primeiro adapter que não existe no legado:

    placement:place_processing_for_buffered_output

O objetivo não é criar uma função específica para "green science". O adapter deve transformar uma necessidade estrutural genérica:

    producer output buffered but unprocessed

em uma opção de construção baseada em:

- factory graph;
- runtime catalog;
- dependency plan;
- placement planner;
- delivery planner;
- power feasibility;
- material availability;
- hard preconditions;
- measurable postconditions.

Primeiro em pure/shadow planning. Somente após testes de invariantes poderá existir transactional execution.

## 13. Decisão

**F2-B: PASS parcial de F2.**

Parity dos dois primeiros handlers foi demonstrada sem alterar a semântica de execução e sem conceder authority live.

F2-C está autorizado.
