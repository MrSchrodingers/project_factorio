# Cortex Research — F2-C Structural Processing Planner

**Phase:** F2 — Action/Option Ontology e Universal Executor
**Checkpoint:** F2-C — structural processing in pure/shadow planning
**Authority:** SHADOW / NO LIVE-WORLD MUTATION
**F1 holdout:** confirmatory seeds 20261101–20261110 remain unspent

## 1. Causal target

F1 replicated the same structural failure in five independent seeds:

    producer_output_unprocessed:output_buffered_not_processed
    -> placement:place_processing_for_buffered_output
    -> no_runner_binding_for_intent

F2-B deliberately preserved that refusal because no legacy handler existed.

F2-C implements the first capability that does not merely wrap an existing runner handler. It is a pure planner that turns a buffered producer deficit into one or more typed processing-cell options.

It does not execute them.

## 2. Constraint against stage-specific solutions

The planner is not given "green science", "iron plate" or a stage target.

Its input is:

- an ActionRequest for place_processing_for_buffered_output;
- observed factory_graph;
- observed world entities;
- deterministic runtime catalog;
- optional available inventory and footprints.

The request may contain only producer IDs, exactly as the F1 counterexample did.

Therefore the planner must infer processing opportunities from physical evidence rather than from curriculum stage names.

## 3. Material inference

For each targeted extraction producer, F2-C walks MATERIAL_RELATIONS in the factory graph.

The relevant evidence boundary is the nearest downstream buffer frontier.

The walk deliberately stops at the first buffer distance. Looking through every reachable buffer would mix materials after networks merge and would make downstream shared storage appear to be the producer's own output.

For each nearest buffer:

- contents absent -> structural_buffer_contents_unobserved;
- observed but empty -> structural_buffer_empty;
- multiple positive materials -> structural_buffer_material_ambiguous;
- exactly one positive material -> material is admitted as observed evidence.

No majority heuristic is used.

This matters in the live F1 world: one buffer contains 399 iron-ore and 12 coal. F2-C refuses that producer as ambiguous rather than assuming the dominant item is the mined material.

## 4. Direct processing recipe selection

F2-C scans the runtime catalog for enabled recipes whose only ingredient type is the observed buffered material.

Examples:

    iron-ore -> iron-plate
    copper-ore -> copper-plate

A multi-input recipe is not accepted as an arbitrary interpretation of a raw material.

Therefore coal does not silently become grenade, plastic or another goal-selected product.

If no one-material processing recipe exists:

    structural_no_direct_processing_recipe

This is intentional. Goal-conditioned product selection belongs to the later cognitive loop, not to a hidden fixed rule in F2.

## 5. Processor selection

The recipe category is resolved against RuntimeFactorioCatalog and DependencyPlanner.machine_for_category.

Selection remains deterministic and uses the planner's existing preference semantics:

- caller preference;
- already available machine;
- measured lower-tier speed;
- stable name tie-break.

For early smelting in the current catalog this resolves to stone-furnace.

## 6. Machine dependency

The processor itself is planned through DependencyPlanner.

The observed character is represented as an available crafting machine only when the world actually contains a character entity.

This avoids an earlier test-only error in which "character" was silently assumed to exist.

Hard precondition:

    processor_constructible

is SATISFIED only when the dependency plan is feasible.

## 7. Joint placement + delivery search

A machine that merely fits is insufficient. The selected machine must also be connectable to the source buffer.

F2-C searches deterministic placement offsets around the buffer. For each candidate:

1. plan_placement checks footprint and occupancy;
2. plan_delivery checks inserter/belt connectivity;
3. only placement+delivery pairs are eligible;
4. candidates are ranked by belt count, displacement and stable coordinates.

This avoids the legacy class of failure where a valid placement leaves no tile for an inserter or belt.

## 8. Structural branch

Each material group becomes a ProcessingBranch carrying:

- material;
- product;
- recipe;
- processor;
- producer IDs;
- buffer IDs;
- selected source buffer;
- machine DependencyPlan;
- PlacementPlan;
- DeliveryLink;
- child ActionRequest;
- hard/soft preconditions;
- predicted postconditions.

The child request preserves parent provenance through parent_action_id.

## 9. Preconditions

Current hard conditions:

- buffer_material_observed;
- direct_processing_recipe_enabled;
- processor_machine_resolved;
- processor_constructible;
- processor_placement_feasible;
- buffer_to_processor_delivery_feasible.

processor_energy_feasible remains UNKNOWN and soft in F2-C.

That is deliberate: F2-C is pure shadow planning. Before any live adapter exists, energy/fuel feasibility must become a measured hard execution precondition.

## 10. Postcondition

The structural ActionRequest preserves the repair prediction:

    physical_factory_graph.producers_reaching_processor
    operator = increase
    state = unknown
    hard = true

Therefore even a future successful transaction cannot be marked ACCEPTED until the graph is remeasured and this postcondition holds.

## 11. Partial plans

One ActionRequest may target multiple producers.

F2-C groups valid targets by observed material. Some branches may be ready while other targets are refused.

Plan states:

- ready: all branches have satisfied hard preconditions and no target refusal;
- partial: at least one branch exists but some target/branch remains unresolved;
- refused: no branch can be constructed.

This prevents one ambiguous producer from discarding a valid independent branch.

## 12. Unit verification

tests/test_cortex_structural.py verifies:

- observed iron-ore buffer -> iron-plate / stone-furnace branch;
- world input remains unchanged;
- multi-material producers become independent branches;
- missing contents are not interpreted as empty;
- ambiguous contents are refused;
- coal does not become an arbitrary multi-input recipe;
- partial plan preserves a valid ore branch while refusing unresolved material.

Focused structural/composition/continuity gate:

    116 passed

including placement, delivery, dependency planning, factory graph, F2-A/F2-B regressions,
phase-state and dashboard context.

Full regression gate:

    1333 core/FLE passed
    2 PyTorch passed
    Ruff/static checks passed
    frontend TypeScript/Vite build passed

## 13. Live shadow audit

Artifact:

    runs/audits/cortex_f2c_live_structural_shadow.json

The live world used for the audit had:

- 125 entities;
- 6 extraction producers;
- 5 producers reaching a buffer;
- 3 producers reaching a processor;
- physical processing coverage = 0.5.

The automatically detected structural targets were:

    u1778
    u1838
    u1839

F2-C produced:

### u1839

Observed buffer:

    u1855
    iron-ore = 429

Plan:

    iron-ore -> iron-plate
    processor = stone-furnace
    placement = build at (35.0, 85.0)
    delivery = one inserter
    all current hard structural preconditions = satisfied

No mutation occurred.

### u1778

Nearest buffer was observed empty.

Refusal:

    structural_buffer_empty

### u1838

Nearest buffer contained:

    coal = 12
    iron-ore = 399

Refusal:

    structural_buffer_material_ambiguous

This is epistemically correct. Choosing iron-ore because it is numerically larger would be an unvalidated heuristic.

## 14. Research implication

F2-C converts the original F1 "no runner binding" into a real generic planning capability for the subset of producer deficits whose material identity is observable.

It also exposes a new information requirement:

> buffer contents are not always sufficient to identify a miner's produced resource.

The next structural improvement should integrate ResourceSurvey/mining-target evidence so a contaminated output buffer can be disambiguated causally.

This is a better research result than hard-coding iron/copper based on the stage.

## 15. Safety status

Still true after F2-C:

- UniversalExecutor live authority: none;
- TransactionalFLEExecutor not invoked by structural planner;
- curriculum_runner unchanged;
- F1 scientific runtime unchanged;
- confirmatory seeds unspent;
- no stage-specific green-science handler added.

## 16. Decision

**F2-C: PASS parcial de F2.**

The replicated structural intent can now be represented and, when physical evidence is sufficient, converted into a concrete placement+delivery processing option without curriculum stage logic.

F2 remains open.

## 17. Next checkpoint — F2-D

F2-D should focus on two things before any live structural execution:

1. integrate ResourceSurvey/mining-resource evidence to disambiguate contaminated producer buffers;
2. compile a ready ProcessingBranch into a Prepared structural action and parity-check its code/purpose/measurement contract against existing FLE primitives.

Only after those gates should the structural path be connected to TransactionalFLEExecutor under controlled EXECUTE authority.
