# Cortex Research — F2-G3 Universal Option Execution Boundary

**Phase:** F2 — Action/Option Ontology and Universal Executor  
**Checkpoint:** F2-G3 — universal Option execution boundary  
**Authority validated:** SHADOW + deterministic transactional fake/replay  
**Live Option EXECUTE:** BLOCKED  
**Implementation commit:** `e25569db40d6e6186cc24b3c380ebdc9dc4e84cf`  
**Confirmatory holdout:** 20261101–20261110 untouched

## 1. Research question

F2-G3 asks whether a composed `ProcessingChainOptionPlan` can cross one generic typed execution
boundary without reintroducing stage-specific dispatch, duplicating rollback, or weakening the hard
functional termination contract validated in F2-F4C.

The intended stack is:

`ProcessingChainOptionPlan -> OptionExecutionBoundary -> StructuralTransactionalAdapter -> TransactionalFLEExecutor`.

The boundary does not plan, compile Factorio code independently, own rollback, or schedule repeated
execution.

## 2. Frozen-plan identity

`option_plan_digest()` computes a canonical SHA-256 over the fully serialized Option plan.

`OptionExecutionGrant` is bound to:

- option id;
- prepared action id;
- exact plan digest;
- code revision;
- run id;
- issuer;
- reason.

A grant cannot be applied to a modified plan without producing
`option_execution_grant_mismatch`.

This prevents a proposal from being inspected in SHADOW and a different mutated payload from being
executed under the same apparent authorization.

## 3. Lineage contract

Before authority is considered, the boundary validates:

`Option -> ActionRequest -> ProcessingBranch -> PreparedStructuralAction`.

The checks include:

- ActionRequest.parent_action_id == Option id;
- ProcessingBranch.parent_action_id == ActionRequest id;
- PreparedStructuralAction.action_id == branch ActionRequest id;
- code revision is identical across Option/Action/Branch;
- run id is consistent when present.

The resulting `OptionExecutionResult` persists this lineage explicitly rather than requiring later
reconstruction.

## 4. Termination contract

F2-G3 does not invent a new success predicate.

The Option termination signature must exactly match the transactional structural contract:

- `physical_factory_graph.producers_reaching_processor INCREASE`;
- `processor_exists EQUALS true`;
- `processor_output INCREASE`.

A mismatch is refused before execution with
`option_execution_termination_invalid`.

This preserves the F2-F3 counterexample: topology alone cannot become functional success.

## 5. Authority semantics

### SHADOW

- validates digest/lineage/termination;
- does not require a runtime;
- does not call the transactional executor;
- `changed_world=false`.

### PROPOSAL

- may represent a promoted candidate;
- still cannot execute;
- `changed_world=false`.

### EXECUTE

Requires all of the following:

- explicit `OptionExecutionGrant`;
- exact grant/plan match;
- explicit transactional executor;
- explicit measurement probe;
- observable Factorio tick source before mutation;
- integer-second-compatible requested tick budget in the current compiler.

World mutation still occurs only inside `StructuralTransactionalAdapter -> TransactionalFLEExecutor`.

There is no scheduler and `continuous_authority=false`.

## 6. Tick semantics

F2-G3 measures `runtime_game_ticks()` immediately before and after the transaction.

Accepted fake execution:

- before: 1000;
- after: 1600;
- observed ticks: 600;
- feedback budget receives `observed_ticks=600`;
- `sustainability_evaluable=true` for that measured fake execution.

Rejected fake execution with rollback:

- transaction advances the fake counter;
- rollback restores the checkpoint and rewinds the counter to 1000;
- post-boundary before/after therefore becomes 1000 -> 1000;
- F2-G3 records `observed_ticks=null`;
- status is `missing_after_rollback`;
- it does **not** convert the rewound counter into a fabricated zero-duration observation.

This is the fail-closed behavior required by the temporal diagnosis of F2-F4C.

## 7. Grant consumption

Within one `OptionExecutionBoundary` instance, a frozen plan digest is consumed at most once.
A second attempt is refused with `option_execution_grant_already_consumed`.

This is intentionally insufficient for live authority.

**Critical limitation:** grant consumption is process-local. Re-instantiating/restarting the process
would lose the consumed-grant set. Therefore F2-G3 does not claim durable one-shot authorization and
does not authorize a live Option canary.

A persistent grant ledger is required before live Option EXECUTE can be considered.

## 8. No legacy runner dependency

Static regression tests parse imports from:

- `factorio_ai_lab.cortex.options`;
- `factorio_ai_lab.cortex.option_execute`.

Neither imports `curriculum_runner`.

The execution path tested in F2-G3 is therefore:

`Option composer -> OptionExecutionBoundary -> StructuralTransactionalAdapter -> TransactionalFLEExecutor`

without runner-stage dispatch.

## 9. Deterministic fake/replay evidence

Canonical artifact:

`runs/audits/cortex_f2g3_option_execution_fake.json`

SHA-256:

`3a2cdf621e2592766cdec3f5a83ca057b42f3b3c184ce356437a1795a4b97f65`

The artifact is generated from the versioned deterministic fake environment in
`tests/test_cortex_option_execute.py`.

Accepted case:

- status=accepted;
- fake_state_mutation=true;
- observed_ticks=600;
- tick_measurement_status=observed;
- feedback budget populated.

Rejected case:

- status=rejected;
- rollback state restored=true;
- rollback tick counter rewound=true;
- observed_ticks=null;
- tick_measurement_status=missing_after_rollback.

Global artifact labels:

- environment=deterministic_fake_transactional;
- factorio_world_mutation=false;
- continuous_authority=false.

This artifact must never be presented as a live Factorio canary.

## 10. Verification

Boundary-focused gate:

- 26 PASS for Option execution/composition/structural transaction;
- Ruff PASS;
- py_compile PASS;
- whitespace PASS.

Integrated focused gate after phase-state/UI hooks:

- 53 PASS;
- Ruff PASS;
- JavaScript syntax PASS;
- live phase-state intentionally remained F2-G2 before this document existed.

Full F2-G3 implementation gate:

- 1421 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

## 11. Scientific decision

**F2-G3 PASS parcial de F2.**

The project now has a generic typed Option execution boundary that can validate a frozen Option,
preserve lineage, enforce the same hard functional termination contract, delegate mutation to the
existing transactional layer, and feed observed game ticks back into the Option budget.

However the original F2 Exit Gate is still not satisfied.

Still open:

- durable/persistent one-shot authorization for live Option execution;
- controlled live functional-chain execution through the Option API;
- formal enforcement that legacy runners are baseline-only;
- sustained autonomous operation.

The original F2 checkbox `transactional execution universal` remains open because G3 proves the
boundary in fake/replay, not durable live operation.

## 12. Next checkpoint — F2-G4A

F2-G4A must implement durable one-shot Option authority **without executing a live canary yet**.

Requirements:

1. persistent grant ledger keyed by grant id + exact plan digest;
2. atomic consume-before-mutation semantics;
3. replay-safe refusal of consumed/stale grants across process restart;
4. explicit expiry/scope fields;
5. no scheduler or continuous grant;
6. integration tests across process/boundary reconstruction;
7. controlled canary runner independent of `curriculum_runner`, but dry-run only in G4A;
8. holdout seeds remain untouched;
9. evolution remains inactive/disabled during controlled validation.

Only after G4A closes may F2-G4B consider one explicit non-confirmatory live Option canary.

F3 remains blocked.
