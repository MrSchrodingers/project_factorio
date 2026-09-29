# Cortex F5-B — Bounded Authority Bridge

Status: **PASS / F5-B closed in test-shadow; no live WORLD mutation**.

## Objective

F5-B proves that F5 can expose a bounded A2 execution capability without
creating ambient EXECUTE authority. The bridge reuses the F2 durable one-shot
Option grant ledger and transactional executor; it does not create a second
authority system.

The invariant is:

Observation -> Option -> Validate -> A2 Grant -> Re-attest WorldLease ->
Execute -> Measure -> Commit/Rollback

## A2 contract

A2 means exactly one transactional Option:

- max_executions = 1;
- exact non-wildcard experiment/world/option scope;
- grant bound to option_id, prepared_action_id, plan digest, code revision and
  run_id;
- positive TTL and explicit expiry;
- durable SQLite persistence;
- atomic grant consumption before runtime mutation;
- second consumption always refused;
- active WorldLease scope must match before validation and immediately before
  execution;
- continuous authority is forbidden;
- learned policy may rank/propose, but cannot issue the grant;
- rejected transactional execution rolls the WORLD state back while the
  one-shot grant remains consumed.

## Evidence strategy

F5-B itself is a control-plane checkpoint. It must not mutate the live WORLD.

The canonical audit runs the executable authority integration tests against a
fake transactional environment and verifies the real production classes:

- factorio_ai_lab.cortex.f5_authority.F5BoundedAuthorityBridge;
- PersistentOptionGrantLedger;
- OptionExecutionBoundary;
- TransactionalFLEExecutor;
- FactorioWorldLease attestation contract.

It additionally requires:

- clean committed source;
- F5-A machine gate PASS;
- ambient authority A0;
- evolution inactive+disabled;
- no active persisted WorldLease;
- empty F5 intervention ledger;
- authority schema A2.max_executions = 1;
- A6 forbidden;
- policy self-grant forbidden.

The audit writes:

    runs/audits/cortex_f5b_authority_bridge.json

No live canary is required to close F5-B. The first physical use of bounded A2
authority belongs to F5-C deterministic autonomous baseline, where physical
capability evidence and rollback semantics are measured under the actual F5
factory protocol.

## Exit gate

F5-B exits when the machine-readable phase state validates the canonical audit
and reports:

- phase5_checkpoint = F5-B;
- phase5_next_checkpoint = F5-C;
- phase5_authority_bridge.validated = true;
- ambient_authority = A0;
- max_bounded_grant = A2;
- continuous_authority = false;
- world_mutation = false;
- grant_issued = false;
- intervention count = 0.

This checkpoint authorizes implementing F5-C. It does not authorize continuous
execution, autonomous scheduling, policy self-grant, or more than one Option per
future A2 grant.

## Closure evidence

F5-B closed on commit 080cd1544da1573cdf21a7c912e7388df45ea383.

Canonical audit: runs/audits/cortex_f5b_authority_bridge.json

SHA-256: 9bcc4328f13e8dabf250bfe3a2d09ffd16994444571217825491ce703737cc2a

Observed closure:

- canonical audit status = pass;
- targeted authority integration = 36/36 PASS;
- all audit checks = true;
- source hashes match the committed implementation;
- ambient authority remains A0;
- maximum bounded grant is A2;
- max_executions = 1;
- continuous authority = false;
- policy self-grant = forbidden;
- evolution = inactive+disabled;
- no active WorldLease;
- F5 intervention ledger count = 0;
- grant issued = false;
- option executed live = false;
- WORLD mutation = false.

Machine-readable transition after regeneration:

- phase5_checkpoint = F5-B;
- phase5_next_checkpoint = F5-C;
- phase5_authority_bridge.validated = true.

F5-B therefore proves the control-plane boundary. It does not claim physical
factory progress. F5-C is the first checkpoint allowed to exercise bounded A2
against the physical deterministic autonomous baseline.

