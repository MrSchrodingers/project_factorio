# Cortex Research — F2-D Resource Identity and Prepared Structural Actions

**Phase:** F2
**Checkpoint:** F2-D
**Authority:** SHADOW / NO LIVE-WORLD MUTATION
**Scientific baseline:** frozen at `95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac`
**Confirmatory holdout:** seeds `20261101–20261110` remain unspent

## 1. Objective

F2-C solved the first structural gap from F1 at planning level, but material identity was still inferred from the contents of the nearest downstream buffer.

That inference is valid only while the buffer contains exactly one material. In the live F2-C audit, producer `u1838` reached a chest containing both `coal` and `iron-ore`, therefore F2-C correctly refused with:

`structural_buffer_material_ambiguous`

The refusal was epistemically correct, but incomplete: a mining producer has a stronger causal identity available from the world itself. The resource tiles under the mining drill determine what the producer can extract.

F2-D introduces that causal evidence and compiles a ready structural branch into an inert, versioned execution contract.

## 2. Research hypothesis

For extraction producers, resource identity observed under the producer footprint is a stronger causal observation than downstream buffer contents.

Therefore:

1. a single observed resource under the producer may identify its output even if its chest is empty;
2. a contaminated chest does not invalidate the producer identity when the mined resource is observed;
3. multiple resource kinds under the producer remain ambiguous;
4. no majority heuristic is allowed;
5. absence of a resource survey does not become a negative observation.

This preserves the central epistemic invariant:

`missing != zero != ambiguous`

## 3. Material-identity precedence

The structural planner now resolves material identity in this order:

1. **ResourceSurvey / producer footprint**
   - one resource kind: observed causal identity;
   - multiple resource kinds: named refusal;
   - no resource evidence: fall through.

2. **Downstream buffer contents**
   - exactly one positive material: observed downstream identity;
   - zero contents: empty refusal;
   - missing contents probe: unobserved refusal;
   - multiple materials: ambiguous refusal.

The planner never chooses the largest stack as a proxy for identity.

## 4. ResourceSurvey integration

The existing `ResourceSurvey` contract from `planning.placement` is reused.

For each extraction producer:

- the entity footprint is computed using the same footprint resolver used by placement;
- surveyed resource tiles intersecting the footprint are collected;
- the unique resource name, when one exists, becomes an `EvidenceRef`;
- the evidence path is recorded as:
  `resource_survey.<producer>.footprint.<material>`.

No new world probe was invented for Cortex. F2-D composes an existing read-only scientific instrument already used by the project.

## 5. BufferedSource evidence

`BufferedSource` now records:

- material;
- producer_id;
- buffer_id;
- observed_count, which may be `None`;
- primary evidence;
- identity_basis;
- optional buffer_evidence.

Valid `identity_basis` values currently used are:

- `mining_resource`;
- `buffer_contents`.

A producer with observed mining identity and an empty buffer may therefore have:

- known material;
- known causal source;
- unknown/zero current buffered amount.

Those are intentionally separate facts.

## 6. Mixed-resource refusal

If a producer footprint overlaps more than one observed resource kind, F2-D returns:

`structural_mining_material_ambiguous`

It does not:

- count tiles and choose the majority;
- inspect the current target stage;
- infer from a requested science pack;
- infer from historical production;
- infer from chest majority.

This is a hard ambiguity until better causal evidence exists.

## 7. PreparedStructuralAction

F2-D adds `structural_prepare.py`.

A `ProcessingBranch` can now be compiled into a `PreparedStructuralAction`.

The object is deliberately inert. It has no execute method and cannot call `TransactionalFLEExecutor`.

### Contract version

`cortex_structural_ops_v1`

### Purpose

`infrastructure`

### Operations

A ready branch currently compiles to semantic operations:

1. `ensure_item`
2. `place_processor` or `adopt_processor`
3. `configure_processing`
4. `connect_delivery`
5. `verify_postconditions`

These are semantic operations, not raw FLE code.

## 8. Measurement contract

Every prepared structural action declares measurement keys:

- `producers_reaching_processor`;
- `physical_processing_coverage`;
- `processor_exists`;
- `processor_status`;
- `processor_output`.

The future EXECUTE adapter must measure these after the transaction.

A prepared action is not an accepted action.

## 9. Preflight contract

The prepared action records:

- material;
- product;
- processor;
- source buffer;
- producers;
- buffers;
- placement plan;
- delivery plan;
- machine dependency feasibility;
- `world_mutation=false`.

A branch with any unsatisfied hard precondition is refused during preparation with:

`structural_branch_precondition_unsatisfied`

## 10. Relationship with TransactionalFLEExecutor

F2-D still stops before execution.

The target architecture is now:

`RepairAction / cognitive candidate`
→ `ActionRequest`
→ `StructuralProcessingPlan`
→ `ProcessingBranch`
→ `PreparedStructuralAction`
→ **future F2-E adapter**
→ `TransactionalFLEExecutor`
→ Factorio
→ measured postconditions
→ `ActionResult`

Only the first five stages exist in F2-D.

## 11. Live shadow audit

Artifact:

`runs/audits/cortex_f2d_live_structural_shadow.json`

The audit used only read-only dashboard observers:

- live world entities;
- live resource overview;
- live prototype catalog;
- factory graph rebuilt from the observed world.

No Factorio mutation occurred.

### Automatically recomputed structural targets

- `u1778`
- `u1838`
- `u1839`

### u1778

F2-C saw an empty downstream buffer.

F2-D observed the producer on coal and resolved:

- material: `coal`;
- identity basis: `mining_resource`;
- buffered count: unknown/not required for identity.

The planner then refused coal structurally with:

`structural_no_direct_processing_recipe`

This is correct. The Cortex did not invent an arbitrary multi-input coal recipe.

### u1838

F2-C refused this producer because its downstream buffer contained:

- coal;
- iron-ore.

F2-D observed the producer footprint over iron ore and resolved:

- material: `iron-ore`;
- identity basis: `mining_resource`;
- observed iron-ore count in downstream buffer: 399.

The previous contamination ambiguity is therefore resolved by stronger causal evidence.

### u1839

Resolved as:

- material: `iron-ore`;
- identity basis: `mining_resource`;
- observed downstream iron-ore: 429.

### Combined structural branch

The two iron producers collapse into one material branch:

`iron-ore -> iron-plate`

Processor:

`stone-furnace`

Chosen structural plan:

- placement: build at approximately `(30, 85)`;
- delivery: direct inserter;
- prepared structural action: ready.

## 12. Scientific interpretation

F2-D changes an important property of the system.

Before F2-D, a downstream mixed buffer destroyed material identity.

After F2-D, the Cortex can distinguish:

- **what the producer causally extracts**;
- **what happens to be present in a downstream buffer**.

This is a move from correlational state reading toward causal state representation.

It is still not cognition by itself, but it is necessary infrastructure for reliable cognition.

## 13. Tests

Focused F2-B/C/D gate verifies:

- ResourceSurvey resolves contaminated buffer causally;
- empty buffer can still have known producer identity;
- mixed-resource footprint is refused;
- no majority heuristic;
- PreparedStructuralAction is inert and versioned;
- prepared operations are deterministic;
- hard precondition failure blocks preparation;
- F2-A/F2-B/F2-C regressions remain green.

## 14. Authority decision

F2-D does **not** grant EXECUTE authority.

Current authority remains:

`SHADOW`

The presence of a ready prepared action means only that:

- evidence is sufficient;
- the structural plan is internally coherent;
- the execution contract is specified.

It does not mean the action has been run or validated in Factorio.

## 15. Decision F2-D

**PASS.**

F2-D is scientifically complete when:

- focused tests pass;
- full core/FLE and ML profiles pass;
- static checks pass;
- documentation/checkpoint state is coherent;
- commit is published;
- dashboard shows F2-D from mechanical phase state;
- frozen F1 scientific runtime remains unchanged.

## 16. Next checkpoint — F2-E

F2-E may implement **controlled transactional execution** of prepared structural actions.

Constraints:

1. explicit `ActionAuthority.EXECUTE`;
2. adapter must consume `PreparedStructuralAction`;
3. execution must go through `TransactionalFLEExecutor`;
4. hard postconditions must be measured;
5. failed postconditions must roll back;
6. no confirmatory seeds;
7. no autonomous continuous authority;
8. first execution must be an isolated canary/replay, not the F1 holdout.

F2-E must not introduce a special-case green-science handler.
