# Cortex Research — F4-B Hybrid Memory Retrieval, Consolidation and Decay

Phase: F4 — Cognitive Memory and Consolidation
Checkpoint: F4-B — hybrid retrieval + consolidation + non-destructive decay
Decision: PASS parcial de F4
Implementation commit: a4ff558eccd9df0898fef4138a75fa1b55976a8d
Authority: SHADOW only
Continuous autonomous authority: OFF
Evolution: inactive + disabled
Confirmatory seeds: frozen / untouched

## Scientific purpose

F4-A created a typed durable memory substrate. F4-B tests whether that substrate can be queried and
summarized through an explicit memory policy without falling back to the legacy stage/recency-only
JSONL recall path.

The checkpoint separates four components:

1. structural validity scope;
2. lexical similarity inside compatible scope;
3. evidence/support/confidence;
4. non-destructive temporal decay.

The canonical replay is read-only. It must leave the memory database byte-level logical manifest
unchanged before versus after the retrieval/consolidation pass.

## Retrieval contract

Implemented in src/factorio_ai_lab/cortex/memory_retrieval.py.

The query contract includes:

- query id;
- optional memory kinds;
- structural scope: stage, symptom, phase, world and tags;
- lexical text;
- explicit result limit;
- optional reference time;
- explicit inclusion/exclusion of unqualified memory.

Known structural mismatches fail closed. Unknown scope does not masquerade as an exact match: it is
kept with lower structural evidence.

Ranking is deterministic and exposes:

- structural score;
- lexical similarity score;
- evidence score;
- decay weight;
- final score;
- machine-readable basis for every contribution.

The current weights are a baseline policy, not an optimized claim.

## Canonical replay

Artifact:

runs/audits/cortex_f4b_memory_retrieval.json

SHA-256:

5fc37b0cee5f121c5ff6b6054fc45f4b0a09bad851e34793958bdd3dc1c5a801

Run:

cortex-f4b-retrieval-20260925T191732Z

Artifact code revision:

a4ff558eccd9df0898fef4138a75fa1b55976a8d, dirty=false.

F4-A source artifact SHA-256:

f45e31785c17cd6222a57937564036dbdd4976ee1d6376b61f340a9d70066228.

## Database invariance

Canonical memory DB before replay:

- schema: cortex_cognitive_memory_v1;
- quick_check=ok;
- item_count=222;
- occurrence_count=759;
- manifest SHA-256:
  e6aa69816fe992f6b2a6afc8aff529fa5f1572106ca0939cee830af5a6cf3399.

Canonical memory DB after replay:

- identical schema;
- identical item count;
- identical occurrence count;
- identical manifest SHA-256.

Therefore retrieval, consolidation and decay did not rewrite or delete canonical memory evidence.

## Canonical query evidence

Four fixed queries were replayed.

### Semantic smelting output

Scope: stage=smelting_probe.

Top memory:

- kind=semantic;
- qualified support=32;
- exact stage scope;
- lexical overlap present;
- total score=0.825.

### Semantic placement error

Same structural scope: stage=smelting_probe.

Changing only the lexical task moves a different memory to rank 1:

- lesson: Entity placement error;
- qualified support=1;
- lexical similarity=0.6666666666666666;
- total score=0.7396479726341121.

This demonstrates that similarity contributes inside a fixed structural scope instead of stage alone
selecting the same row.

### Fuel-starvation procedure

Scope: symptom=fuel_starved:no_fuel.

Top memory:

- kind=procedural;
- action=resupply:insert_fuel_from_world_container;
- qualified support=54;
- empirical Wilson lower-95 confidence=0.7126323220121027;
- structural exact symptom match;
- lexical overlap present;
- total score=0.76.

### Electric transition route-buffer counterexamples

Scope:

- stage=Electric mining transition;
- phase=route_buffer.

All returned rows obey the explicit stage+phase scope. The highest-support signature has 8 qualified
occurrences.

## Consolidation snapshot

The consolidation pass is a read-only summary over the durable store.

Observed:

- semantic items=134;
- semantic support=566;
- semantic qualified support=530;
- repeated semantic items=70;
- duplicate semantic support beyond first occurrence=432;
- counterexample items=33;
- counterexample support=85;
- repeated counterexample items=10;
- procedural items with empirical confidence=1.

Consolidation does not delete original occurrences or collapse provenance.

## Decay policy

Policy:

cortex_non_destructive_decay_v1

The decay probe uses a 180-day half-life with support protection and a 0.05 minimum weight.

For the same old observation:

- qualified support=1 -> weight=0.3339200384203678;
- qualified support=50 -> weight=0.998542956000045.

Decay is therefore support-protective and non-destructive. This is a baseline forgetting policy, not
an optimality claim.

## Gate evidence

Focused control-plane/retrieval/UI gate:

- 42 tests PASS;
- Ruff PASS;
- JavaScript syntax PASS;
- py_compile PASS;
- whitespace PASS.

Full implementation gate:

- 1520 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

Promotion is fail-closed:

- implementation alone does not promote F4-B;
- artifact alone does not promote F4-B;
- F4-A must remain valid;
- this document must exist;
- F4-B artifact must be PASS on a clean revision;
- database_before must equal database_after;
- F4-A source artifact hash must match;
- all canonical retrieval checks must remain true.

## Claim boundary

F4-B proves:

- hybrid structural+lexical retrieval is explicit and reproducible;
- known scope mismatch is rejected;
- lexical content can change rank inside the same structural scope;
- procedural memory can outrank generic semantic memory under an exact symptom;
- counterexamples are retrievable by structural scope;
- repeated support is visible through consolidation without provenance destruction;
- decay can be non-destructive and support-protective;
- the canonical replay is read-only.

F4-B does not prove:

- retrieval improves Factorio task outcome;
- the ranking weights are optimal;
- decay hyperparameters are optimal;
- memory causes cross-seed transfer improvement;
- causal benefit from memory;
- F4 Exit Gate completion.

## Decision

F4-B: PASS parcial.

F4 remains ACTIVE in SHADOW.

Next checkpoint: F4-C — causal memory ablation + transfer benchmark.

F4-C must use a held-out transfer protocol with leakage control and compare the same decision/task
surface with memory available versus memory ablated. F4 closes only if memory removal causes a
statistically detectable performance loss. Confirmatory seeds remain frozen until that protocol is
defined and frozen.


## Post-closure F4-C readiness result

A subsequent eligibility audit did not promote F4-C. The existing corrected exploratory corpus is
too homogeneous for a defensible causal transfer claim.

Canonical protocol/readiness document:

docs/CORTEX_PHASE4_CAUSAL_ABLATION_PROTOCOL.md

Machine state must therefore remain F4 ACTIVE / F4-B, with F4-C as the next checkpoint and the F4
Exit Gate open. Confirmatory seeds remain frozen.
