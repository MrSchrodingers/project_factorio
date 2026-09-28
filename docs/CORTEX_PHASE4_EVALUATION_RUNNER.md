# F4-C held-out evaluation runner

This gate is frozen before the first held-out evaluation outcome.

## Scientific boundary

The evaluation runner reuses the exact treatment and protocol task-world
semantics validated during pilot instrumentation. The following hashes are
locked and must not change during the 20 held-out pairs:

- causal_treatment.py: d412ed9af1cfa75def10338a6dfbec97c60cf4ce792645b6d38747c3f47d3469
- causal_pilot_runtime.py: 0d64659a8c932592c10b9af9476cfa3cac1af0e2a54ee5953febac295094e085
- treatment audit: c833bd2a979da2370f3955f48df20f05fe7562efeede50e2e41b269375c63767
- pilot-runner audit: 1e9f47159d9abcc896a632b49d86ed4ff18fc208f79d5f19f346a42b0ddc2dd8

Pilot outcomes are never used for adaptation.

## Held-out execution

The fixed sequence is 20261221 through 20261240, exactly five pairs per family
and the frozen 10/10 arm-order schedule from the preregistration manifest.

Each pair reconstructs the frozen task, restores the same checkpoint before
both arms, applies MEMORY ON versus retrieval-only MEMORY ABLATED, uses
identical non-memory candidates/tools/budgets/outcome extraction, writes only
quarantined arm traces, never mutates the live Factorio world, and receives a
canonical review before the next fixed seed.

A technically invalid pair is retained, is not replaced, and does not enter
primary inference. A hard provenance or integrity failure halts the sequence.

## Frozen inference

Before outcomes, the implementation freezes mean paired delta_J, exhaustive
one-sided exact paired sign-flip inference, exact 95% confidence-set inversion,
median delta_J, paired Cohen dz, SESOI 0.05, minimum 16 valid pairs overall and
minimum 3 valid pairs per family.

Pilot and confirmatory rows are excluded from primary inference. No confirmatory
seed is authorized by this gate.
