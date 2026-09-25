# Cortex Research — F2-G4B Live Option Canary

**Phase:** F2 — Action/Option Ontology and Universal Executor
**Checkpoint:** F2-G4B — one-shot live Option execution
**Decision:** **PASS parcial de F2**
**Live run:** cortex-f2g4b-20260925T014418Z
**Seed:** 424242 — non-confirmatory
**Implementation commit:** 794963b435bff616042ca0a6e6f278ead315e5e0
**Temporal instrumentation fix:** aaf10beb5b5ec11b7b28e3619823b02b0a465b59
**Continuous authority:** OFF
**Automatic retry:** false
**Execution attempts:** exactly 1

## 1. Scientific question

F2-G4B asks whether the first Cortex processing-chain Option can cross the complete live path:

ProcessingChainOptionPlan
→ OptionExecutionBoundary
→ persistent one-shot grant
→ attested FactorioWorldLease
→ StructuralTransactionalAdapter
→ TransactionalFLEExecutor
→ live Factorio world

without curriculum_runner, without a reusable grant, without a scheduler and without touching the
frozen confirmatory seeds.

## 2. Preconditions proven before live mutation

The live runner refused execution unless all of these were true:

- clean committed source tree;
- exact code revision;
- F2-G4A persisted as the current validated checkpoint;
- confirmatory seed guard passed;
- canonical G4B artifact did not already exist;
- no persisted active WorldLease existed;
- factorio-ai-evolution was inactive and disabled;
- timing/TTL parameters were positive;
- execution required explicit --execute.

The read-only preflight passed on implementation commit:

794963b435bff616042ca0a6e6f278ead315e5e0

and reported:

- seed=424242;
- confirmatory_seed=false;
- phase2_checkpoint=F2-G4A;
- g4a_validated=true;
- world_mutation=false;
- grant_issued=false;
- live_option_execute_authorized=false.

## 3. WorldLease attestation

G4B strengthened FactorioWorldLease before the live run.

Each acquisition now receives a unique lease_id. active_attestation() verifies:

- this process still holds the lock handle;
- state status=active;
- current PID;
- exact run_id;
- exact arena;
- exact owner;
- exact lease_id.

The grant scope is bound to:

arena : run_id : lease_id

For the live run:

lease_id:

c2ecac436ca641b68d4b8e20962588b7

scope:

cortex_f2g4b_option_live_canary:cortex-f2g4b-20260925T014418Z:c2ecac436ca641b68d4b8e20962588b7

The same scope was re-attested immediately before OptionExecutionBoundary.execute().

After the run, the persisted lease state is released.

## 4. One-shot authority

Grant id:

8a7c449946224b458a04820b3e4d8288

Plan digest:

3319fdcae56a30a3785c6e64cf2976c442a6c7ed51d3d7f68e19686ef9db9fec

Before execution:

- consumed_at=null;
- consume_result=null.

Before runtime mutation, the ledger atomically committed:

- consumed_at=2026-09-25T01:44:58.837313+00:00;
- consume_result=reserved_before_runtime_mutation.

The same values are present in OptionExecutionResult and in the persisted ledger.

No second grant and no second execution were issued.

## 5. Live functional result

Canonical live artifact:

runs/audits/cortex_f2g4b_option_live_canary.json

SHA-256:

fb9b69b38a3446dd956ebf529f1888bebfb670cfe59fa8fd8b24b74030f0fc95

Result:

- status=completed;
- option status=accepted;
- changed_world=true;
- transaction_committed=true;
- rollback_observed=false;
- functional_accept=true;
- continuous_authority=false;
- automatic_retry=false;
- option_execution_attempts=1.

Hard functional postconditions were all satisfied:

1. producers_reaching_processor increased;
2. processor_exists=true;
3. processor_output increased.

Measured transition:

Before:

- physical_processing_coverage=0.0;
- processor_exists=false;
- processor_output=0;
- producers_reaching_processor=0.

After:

- physical_processing_coverage=1.0;
- processor_exists=true;
- processor_output=13 iron plates;
- producers_reaching_processor=1;
- processor_status=no_fuel.

Therefore this live run proves a functional chain constructed through the generic Cortex Option
boundary under persistent one-shot authority.

## 6. Sustainability boundary

The furnace ended no_fuel.

Therefore:

functional_accept = true

but:

sustained_operation = false

and:

sustainability_classification = functional_accept_terminal_no_fuel

G4B is not evidence of sustainable autonomy.

## 7. Temporal counterexample

The immutable live artifact records:

- ticks_before=21840;
- ticks_after=7800;
- observed_ticks=null;
- tick_measurement_status=invalid_rewound.

This initially looked like an accepted transaction rewinding time.

Source audit of FLE 0.4.3 showed a different cause:

- an Action carrying game_state causes environment.step() to call reset_instance(game_state);
- GameState serializes entities, inventories, research, namespaces/messages, but not storage.elapsed_ticks;
- the FLE reset path sets storage.elapsed_ticks=0.

Consequently, ticks_before and ticks_after belong to different counter epochs whenever a checkpointed
Action restores its GameState before execution.

The original live artifact was intentionally not rewritten.

Temporal audit:

runs/audits/cortex_f2g4b_temporal_audit.json

SHA-256:

21cdcbe0e60751952600ae1edb26ab4d0d94e72c9fff5d18e3d83f1f023e52d1

Classification:

functional_accept_tick_epoch_reset_explained

No second live canary was executed.

## 8. Temporal instrumentation fix

Commit:

aaf10beb5b5ec11b7b28e3619823b02b0a465b59

StructuralTransactionalAdapter now records:

- whether checkpoint execution was used;
- executor_step_ticks from FLEStep.info.ticks when valid.

OptionExecutionBoundary now applies conservative semantics:

- rejected/rolled-back transaction → no OptionBudget temporal feedback;
- accepted monotonic counter → ordinary before/after delta;
- accepted checkpoint reset with valid FLE step ticks → observed_checkpoint_epoch;
- accepted rewind without transaction-local ticks → invalid_rewound.

The deterministic checkpoint-reset test reproduces:

ticks_before=21840
ticks_after=7800

and recovers transaction-epoch evidence without fabricating a cross-epoch delta.

The G4B live artifact remains immutable and retains its original invalid_rewound classification.

## 9. Verification gates

G4B implementation gate before the live run:

- 1459 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript PASS;
- TypeScript/Vite PASS;
- whitespace PASS.

Temporal-fix gate after the live counterexample:

- 1460 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript PASS;
- TypeScript/Vite PASS;
- whitespace PASS;
- canonical live artifact SHA unchanged.

## 10. What G4B proves

G4B proves:

- persistent one-shot authority can be used on the live Factorio path;
- the grant is tied to an attested live WorldLease acquisition;
- the generic Option boundary can construct a functional processing chain live;
- curriculum_runner is not on the execution path;
- hard postconditions can accept the live transaction;
- the grant is consumed durably before world mutation;
- exactly one authorized live Option attempt occurred.

G4B does not prove:

- sustainable operation;
- closed-loop autonomous executive behavior;
- exactly-once distributed world effect after a post-consume process crash;
- memory transfer;
- learned policy superiority;
- world-model benefit;
- legacy runner baseline-only enforcement.

## 11. F2 status after G4B

The original F2 checklist now has:

- primitive action schemas: complete;
- typed ActionRequest/ActionResult/Refusal/EvidenceRef: complete;
- generic facades and pre/postconditions: complete;
- provenance/refusals: complete;
- transactional execution universal: satisfied by persistent live G4B path;
- initial Options: complete;
- functional chain without curriculum_runner: satisfied by G4B;
- legacy runner executable only as baseline: still open.

Therefore F2 remains ACTIVE.

## 12. Next checkpoint — F2-G5

F2-G5 should formalize legacy-runner baseline-only enforcement.

The goal is not to delete the legacy runner. It remains valuable as the experimental baseline.

G5 must ensure the production/research Cortex path cannot silently dispatch curriculum_runner or
other stage-coded legacy control as an authority path.

Minimum acceptance criteria:

1. explicit execution-mode boundary between baseline and Cortex;
2. legacy curriculum runner allowed only under a baseline-labelled mode;
3. Cortex live/Option entry points fail closed if asked to route through legacy stage handlers;
4. tests proving no curriculum_runner import/dispatch from Cortex control paths;
5. dashboard/phase-state make the baseline-only distinction visible;
6. confirmatory seeds remain untouched;
7. evolution remains inactive/disabled;
8. F2 Exit Gate is reassessed only after this enforcement is green.

F3 remains blocked until F2 is formally closed.
