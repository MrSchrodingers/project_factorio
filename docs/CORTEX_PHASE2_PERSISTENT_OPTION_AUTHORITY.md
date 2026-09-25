# Cortex Research — F2-G4A Persistent One-Shot Option Authority

**Phase:** F2 — Action/Option Ontology and Universal Executor
**Checkpoint:** F2-G4A — persistent one-shot Option authority
**Decision:** **PASS parcial de F2**
**Authority validated:** persistent grant lifecycle + restart/concurrency + deterministic dry-run
**Live Option EXECUTE:** **NOT EXECUTED / still blocked in G4A**
**Implementation commit:** f567bf453c9e3c0e8dfb319adfeef266b4926af8
**Confirmatory holdout:** 20261101–20261110 untouched

## 1. Research question

F2-G4A asks whether the generic OptionExecutionBoundary proven in F2-G3 can receive
**durable, one-shot, replay-safe execution authority** without relying on process-local memory and
without yet mutating a live Factorio world.

F2-G3 could refuse a second execution only while one Python boundary instance remained alive.
That property was insufficient for a live control boundary because restart/reconstruction erased the
consumed set.

G4A changes the authority invariant from process memory to durable transactional state.

## 2. Persistent ledger

Canonical implementation:

- src/factorio_ai_lab/cortex/grant_ledger.py;
- SQLite transactional store;
- journal_mode=WAL;
- synchronous=FULL;
- busy_timeout=10000;
- explicit ledger schema version cortex_option_grant_ledger_v1;
- explicit grant schema version cortex_option_execution_grant_v1;
- no duplicate-grant upsert.

A durable grant records at least:

- grant_id;
- option_id;
- prepared_action_id;
- exact canonical plan_digest;
- code_revision;
- non-empty run_id;
- issued_at;
- expires_at;
- exact scope;
- issued_by;
- reason;
- schema_version;
- consumed_at;
- consume_result.

OptionExecutionScope is explicitly non-wildcard and contains:

- exact experiment id;
- exact world/lease id;
- exact Option kind;
- max_executions=1.

Wildcard/glob scope is rejected at construction.

## 3. Atomic consume-before-mutation

PersistentOptionGrantLedger.consume() uses a write transaction beginning with BEGIN IMMEDIATE.

Inside the same transaction it:

1. resolves the exact grant_id;
2. compares the full persisted grant with the supplied grant;
3. refuses already-consumed authority;
4. validates expiry;
5. atomically writes consumed_at + consume_result;
6. commits;
7. reloads and verifies durable consumption.

Only after that method returns an allowed durable consumption does
OptionExecutionBoundary.execute() call:

ProcessingChainOptionPlan -> OptionExecutionBoundary -> StructuralTransactionalAdapter -> TransactionalFLEExecutor

Therefore the causal ordering is:

digest/lineage/scope/runtime preflight -> expiry + atomic durable consume -> runtime mutation

and never runtime mutation -> consume.

## 4. Crash semantics

G4A deliberately chooses **fail-closed at-most-once authority**.

If the process crashes after durable consumption but before runtime mutation, the grant remains
consumed after restart. The system does **not** automatically reuse it.

This prevents duplicate world mutation at the cost of a possible lost execution.

G4A does **not** claim distributed exactly-once coupling between SQLite and the external Factorio
world. Such a claim would require a stronger cross-system transaction/reconciliation protocol.

## 5. Restart and concurrency evidence

Tests cover:

- persist grant -> reconstruct ledger -> resolve grant;
- execute once -> reconstruct boundary/ledger -> second execution refused;
- manual durable consume -> simulated crash window -> reconstructed execution refused;
- expired grant refused;
- wrong prepared action refused;
- wrong plan digest refused;
- wrong code revision refused;
- wrong run id refused;
- wrong exact scope refused;
- wildcard scope refused;
- process-local EXECUTE without persistent ledger refused;
- duplicate grant_id refused rather than upserted;
- concurrent double-consume using independent SQLite connections;
- concurrent double-consume using **spawned OS processes**.

For each double-consume race, the required result is exactly one consumed and one already_consumed.

## 6. Dry-run runner

Canonical runner:

scripts/run_cortex_option_authority_dry_run.py

It is intentionally independent of curriculum_runner.

The runner:

1. composes a deterministic establish_processing_chain Option fixture through Cortex APIs;
2. creates an exact non-wildcard scope;
3. issues and persists one grant;
4. validates the exact grant through OptionExecutionBoundary.validate();
5. executes a scope-mismatch negative control;
6. re-reads the ledger;
7. persists an audit artifact.

It deliberately does **not**:

- create a Gym/FLE environment;
- connect to RCON;
- acquire live Factorio execution authority;
- call OptionExecutionBoundary.execute();
- consume the grant;
- mutate Factorio;
- use a confirmatory seed;
- enable evolution.

## 7. Canonical dry-run evidence

Artifact:

runs/audits/cortex_f2g4a_option_authority_dry_run.json

SHA-256:

94b60b7b8a298252edcb37b4435854c97f83e0f6832de9ec46aec05aff1e1ec6

Run:

cortex-f2g4a-dry-20260925T011524Z

Artifact code revision:

f567bf453c9e3c0e8dfb319adfeef266b4926af8

Observed invariant summary:

- status=pass;
- factorio_environment_created=false;
- factorio_rcon_used=false;
- factorio_world_mutation=false;
- continuous_authority=false;
- live_option_execute_authorized=false;
- exact grant validation valid=true;
- ledger status before consumption ready;
- wrong-world scope negative control refused with option_execution_grant_mismatch;
- consumed_at=null;
- consume_result=null.

The persisted dry-run grant uses the synthetic scope
world_lease_id=dry-run:no-live-factorio-world, so it cannot represent live-world authority.

## 8. Phase-state and UI gate

scripts/cortex_phase_state.py does not promote F2-G4A merely because this document exists.

G4A is reconstructed only when all of the following are true:

- this G4A document exists;
- canonical dry-run artifact exists and is readable;
- artifact status is pass;
- factorio_world_mutation=false;
- continuous_authority=false;
- live_option_execute_authorized=false.

The dashboard labels the checkpoint:

F2-G4A · persistent one-shot authority · DRY-RUN

and explicitly states:

durable ledger · no live EXECUTE.

The F2-F4C cell remains the last accepted live Cortex execution evidence.

## 9. Verification

Final implementation full gate before promotion:

- **1440 core/FLE PASS**;
- **2 PyTorch PASS**;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS;
- phase-state before closure remained F2-G3, proving code alone did not self-promote.

Focused authority/continuity tests additionally exercise persistent restart, spawned-process
concurrency, dry-run non-consumption and phase-state gating.

## 10. Scientific decision

**F2-G4A PASS parcial de F2.**

The project now has durable, auditable, one-shot Option grant authority semantics that survive
process reconstruction and refuse concurrent/replayed consumption.

This closes the specific F2-G3 limitation that authority consumption lived only in Python process
memory.

It does **not** prove:

- live Option-controlled execution;
- live Option-controlled functional processing chain;
- sustainable autonomous operation;
- closed-loop Cortex executive;
- exactly-once world effect across a post-consume crash;
- legacy runner baseline-only enforcement.

The original F2 Exit Gate therefore remains open.

## 11. Next checkpoint — F2-G4B

Only after G4A publication/deploy verification may G4B execute **one explicit non-confirmatory live
Option canary**.

G4B must require:

1. exact persistent one-shot grant;
2. non-wildcard scope bound to the actual experiment/world lease;
3. FactorioWorldLease;
4. no scheduler and no automatic retry;
5. pre-mutation tick source;
6. atomic consume before mutation;
7. existing transactional commit/rollback;
8. hard functional termination contract unchanged;
9. tick/provenance artifact;
10. confirmatory seeds untouched;
11. evolution inactive/disabled;
12. continuous_authority=false.

A timeout is not permission to retry. Process, WorldLease, ledger, artifact and transaction result
must be inspected first.

F3 remains blocked.
