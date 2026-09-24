# Cortex Research — F2-G2 Processing Chain Option

**Phase:** F2 — Action/Option Ontology and Universal Executor
**Checkpoint:** F2-G2 — first temporally extended Option
**Authority:** SHADOW / replay only
**Implementation commit:** `9c57b7b1fa8b804113d77044df8cf0c3feba4355`
**Source live evidence:** F2-F4C functional accept
**Confirmatory holdout:** 20261101–20261110 untouched

## 1. Research question

F2-G2 asks whether the already validated structural, processor-energy and delivery-actuator primitives can be composed into a temporally extended Option without reintroducing a stage-coded runner.

The Option follows the classical initiation-policy-termination decomposition while preserving Cortex-specific provenance, evidence and authority boundaries.

## 2. Option schema

`OptionRequest` contains:

- option_id;
- OptionKind;
- goal;
- ActionProvenance;
- OptionBudget;
- explicit ActionAuthority;
- evidence references.

The first stable kind is:

`establish_processing_chain`

F2-G2 rejects EXECUTE authority. It is restricted to SHADOW/PROPOSAL composition.

## 3. Internal policy / children

The processing-chain Option composes existing components rather than duplicating their handlers:

1. `factorio_ai_lab.cortex.structural` — choose one processing branch;
2. `factorio_ai_lab.cortex.structural_prepare` — build the inert v1 contract;
3. `factorio_ai_lab.cortex.functional_dependency` — satisfy processor energy, producing v2;
4. `factorio_ai_lab.cortex.delivery_actuator_dependency` — satisfy delivery-actuator energy, producing v3.

`OptionStep` records requires/provides/component/details for each child.

Multiple material branches are not silently ranked in F2-G2. Ambiguity becomes the named refusal `option_processing_branch_ambiguous`.

## 4. Initiation, effects and termination

Initiation conditions are inherited from the selected structural branch.

Predicted effects and termination conditions include:

- `physical_factory_graph.producers_reaching_processor INCREASE`;
- `processor_exists EQUALS true`;
- `processor_output INCREASE`.

This is deliberate. F2-F3 proved that topology alone can pass while functional output remains zero.

## 5. Provenance lineage

F2-G2 requires the Option and its child ActionRequest to share code-revision/run lineage.

The child ActionRequest is re-parented to the Option id before structural planning. Structural planning then creates the branch request as a child of that ActionRequest.

Lineage therefore becomes:

`Option -> ActionRequest -> ProcessingBranch/PreparedStructuralAction`.

Mixed code revision/run lineage is refused with `option_provenance_mismatch`.

## 6. Tick budget semantics

`OptionBudget` separates requested time from observed Factorio game time.

Fields include:

- requested_ticks / requested_seconds;
- observed_ticks / observed_seconds / observed_source;
- effective_ticks;
- planning_ticks / planning_seconds / planning_source;
- sustainability_evaluable;
- budget_overrun.

Rules:

- requested ticks are never reported as observed;
- tick counts must be integers;
- when no observed horizon exists, planning uses the requested budget and sustainability remains non-evaluable;
- when an observed horizon exists, energy planning uses `max(requested_ticks, observed_ticks)`;
- observed time may extend fuel sizing but cannot reduce the requested floor.

A 3000-tick observed horizon resizes the F2 test fixture from one coal unit to two for the furnace and three for the burner inserter.

## 7. Generic tick instrumentation

`runtime_game_ticks()` now lives in `factorio_ai_lab.instrumentation.runtime`.

It accepts either an environment or runtime instance and returns measured elapsed Factorio ticks, or `None` if unavailable/malformed.

`curriculum_runner._game_ticks()` remains only as a compatibility wrapper around this generic instrument.

Cortex does not import the legacy runner.

## 8. Historical replay

Canonical replay artifact:

`runs/audits/cortex_f2g2_option_contract_replay.json`

SHA-256:

`b05b20cc0070dcb16180757b927702e790947bb686913522af30499a20a59d21`

Replay source:

`runs/audits/cortex_f2f4c_structural_canary.json`

Observed replay result:

- status=pass;
- authority=shadow;
- world_mutation=false;
- continuous_authority=false;
- source processor output=13;
- source final processor status=no_fuel;
- requested budget=600 ticks;
- effective/observed ticks=null;
- sustainability_evaluable=false;
- parent/child action lineage valid;
- v3 prepared contract valid;
- processor and delivery dependencies ready.

## 9. Replay limitation

F2-F4C stored instrument labels but not the raw historical world/catalog/resources/inventory payloads.

Therefore the historical artifact cannot support a full planner re-execution without substituting later live state.

F2-G2 explicitly records:

- planner_reexecuted=false;
- historical_inputs_replayable=false;
- full planner replay is not claimed.

The complete composer is instead exercised by deterministic tests over preserved F2 planners. This distinction prevents a partial contract replay from being reported as a full world replay.

## 10. Verification

Focused F2-G2 gate:

- 48 tests PASS;
- Ruff PASS;
- py_compile PASS;
- historical contract replay PASS;
- world_mutation=false.

Full F2-G2 gate:

- 1413 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

## 11. Scientific decision

**F2-G2 PASS parcial de F2.**

The project now has an initial typed temporally extended Option that composes already validated primitives and dependencies, carries provenance, has initiation/effect/termination semantics and models time/energy in game ticks.

However F2 is not complete:

- the Option is not yet executed through a universal Option execution boundary;
- the historical source artifact cannot provide a full planner replay;
- sustained operation is not proven;
- the legacy runner has not yet been formally constrained to baseline-only status.

## 12. Next checkpoint — F2-G3

F2-G3 must implement a universal Option execution boundary, first in SHADOW/replay and transactional fake/replay environments.

Requirements:

1. execute a `ProcessingChainOptionPlan` through one generic typed boundary;
2. reuse `StructuralTransactionalAdapter` / `TransactionalFLEExecutor` rather than duplicate rollback;
3. measure `runtime_game_ticks()` before/after execution and feed observed ticks back into OptionBudget;
4. preserve Option -> Action -> transaction provenance;
5. keep hard functional termination conditions unchanged;
6. prove no `curriculum_runner` dependency in the functional-chain integration test;
7. keep EXECUTE explicit and one-shot only after a later gate;
8. do not use confirmatory seeds;
9. do not enable continuous autonomous authority.

F3 remains blocked until the original F2 Exit Gate is satisfied.
