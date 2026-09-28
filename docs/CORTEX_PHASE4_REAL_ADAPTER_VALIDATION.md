# F4-C Real-Family Adapter Validation

## Scope

This gate validates the four frozen F4-C task-family bindings against the
project's real planning and execution components without executing any
preregistered pilot, evaluation, or confirmatory seed.

The validation mode is real_adapter_preflight. Every task uses the dedicated
adapter_preflight partition with seed null. Any protocol partition or seeded
task is refused by the adapter before the family component runs.

This gate is infrastructure evidence only. It does not estimate the causal
effect of memory and does not satisfy the F4 causal exit gate.

## Family bindings

- spatial_logistics_routing -> planning.astar.weighted_astar
- fuel_energy_recovery -> learning.repair_loop.plan_repairs plus
  planning.resupply.plan_supply
- structural_flow_repair -> prepare_structural_branch ->
  compile_structural_action -> StructuralTransactionalAdapter over
  TransactionalFLEExecutor
- production_transition_planning -> ProductionDagPlanner plus
  DependencyPlanner

The structural family deliberately exercises EXECUTE semantics only inside a
checkpointed disposable in-memory environment. It does not create a real FLE
environment, acquire a world lease, create a live execution grant, call RCON,
or mutate the Factorio world.

## Required invariants

The audit passes only when all four families demonstrate:

1. exact paired checkpoint restoration before MEMORY ON and MEMORY ABLATED;
2. retrieval-only ablation with identical candidate, tool, budget, and outcome
   extractor surfaces;
3. source memory unchanged and all adapter writes quarantined;
4. recomputable J and delta_J;
5. all success-fixture hard postconditions satisfied;
6. protocol/evaluation partition tasks rejected before execution;
7. family-specific negative probes remain visible instead of becoming false
   successes.

The negative probes are an insufficient route-resource budget, fuel drawn from
an active chain, structural creation of a new dead end with rollback, and an
insufficient production material budget.

## Epistemic boundary

A PASS establishes that the paired harness can call the project's real
family-specific components safely and deterministically in NON-PROTOCOL
preflight. It does not establish a positive memory effect, transfer to held-out
Factorio tasks, pilot success, evaluation success, confirmatory evidence, or
F4 exit-gate completion.

After this gate passes, execution_ready may become true for the controlled
F4-C pilot stage. Evolution and continuous authority remain off, and
confirmatory seeds remain frozen.
