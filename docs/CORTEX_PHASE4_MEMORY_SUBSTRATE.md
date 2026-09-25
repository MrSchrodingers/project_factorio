# Cortex Research — F4-A Typed Cognitive Memory Substrate

Phase: F4 — Cognitive Memory and Consolidation
Checkpoint: F4-A — typed memory substrate + auditable historical migration
Decision: PASS parcial de F4
Implementation commit: 756702bb6961fe8c989b2658b0a020200b7632bf
Authority: SHADOW only
Continuous autonomous authority: OFF
Evolution: inactive + disabled
Confirmatory seeds: frozen / untouched

## Scientific purpose

F3 made the executive decision loop explicit. F4 asks a different causal question: can experience be
stored, retrieved, consolidated and transferred in a form that changes later decisions?

F4-A does not answer the causal question yet. It creates the substrate needed to ask it without
equating append-only logs with cognitive memory.

The checkpoint separates:

- bounded transient working memory;
- durable episodic memory;
- durable semantic memory;
- durable procedural memory;
- counterexamples as first-class memory.

Every durable occurrence keeps source provenance. Missing confidence remains unknown rather than
being coerced to zero.

## Typed substrate

Implementation:

src/factorio_ai_lab/cortex/memory.py

Persistent store:

runs/ledger/cortex_cognitive_memory.sqlite3

Schema:

cortex_cognitive_memory_v1

The store uses:

- SQLite WAL;
- synchronous=FULL;
- foreign_keys=ON;
- BEGIN IMMEDIATE for writes;
- stable memory IDs;
- immutable item digests;
- stable occurrence IDs;
- source path + source snapshot digest + source locator;
- occurrence payload SHA-256;
- qualified support separated from raw support;
- contradiction count;
- explicit validity scope;
- explicit confidence method and sample count;
- deterministic batch manifests.

Future batches may append to the database without invalidating the F4-A migration. The control plane
recomputes the original batch manifest from persisted occurrences and item digests.

## Working memory

The F4-A probe uses capacity=3 and inserts four executive slots:

1. goal;
2. belief;
3. candidate;
4. prediction.

The resulting bounded working memory contains belief/candidate/prediction and confirms that the
oldest goal slot was evicted. It is explicitly non-persistent.

This validates the data structure and boundedness only. It does not yet show that working memory
improves decision quality.

## Canonical migration

Artifact:

runs/audits/cortex_f4a_memory_substrate_migration.json

SHA-256:

f45e31785c17cd6222a57937564036dbdd4976ee1d6376b61f340a9d70066228

Run / deterministic batch:

cortex-f4a-af2a612bb1327f0b2fb2

Artifact implementation revision:

756702bb6961fe8c989b2658b0a020200b7632bf, dirty=false.

Batch manifest:

- occurrences: 759;
- SHA-256:
  cfe4472fc53d8f20f9ee4bf3e16951d8689d3bd7d381e7c5d663ae4f61637e71.

The live database independently recomputes the same count/hash and PRAGMA quick_check=ok.

## Source evidence

### Executive episode ledger

Source:

runs/ledger/cortex_executive_episodes.sqlite3

Snapshot SHA-256:

e1b00696e37cab0b2ad2330a91015cfeca40024e6f18e7b51cd3f42a1dc8aa8f

Episodes:

54.

All 54 become episodic memories. Procedure support is imported only where F3-B credit is eligible
and numeric.

### Verified knowledge log

Source:

runs/knowledge.jsonl

Source SHA-256:

174331b4f4622b3a95efc515bf4968e9f25fd06f8c5e9e62877cb7f96e7b50bc

Observed population:

- total rows: 929;
- verified rows: 566;
- unverified rows: 363;
- malformed rows: 0;
- verified rows with qualified provenance: 530.

The 566 verified occurrences consolidate to 134 semantic memory identities. Duplicates increase
support; they are not discarded as provenance.

The 36 verified rows lacking usable evidence_keys remain occurrences but do not count as qualified
support.

### Counterexample log

Source:

runs/counterexamples.jsonl

Source SHA-256:

be72bffb804faa7138a8fe2f1c287d46ab8bc6b517be43e978f8c7cfc82969aa

Observed population:

- rows: 85;
- malformed rows: 0;
- qualified rows: 85;
- typed counterexample memory identities: 33.

Each source occurrence remains individually attributable while repeated signatures/scopes aggregate
support.

## Canonical memory population

After the F4-A migration:

| Kind | Items | Occurrences | Qualified occurrences |
| --- | ---: | ---: | ---: |
| episodic | 54 | 54 | 54 |
| semantic | 134 | 566 | 530 |
| procedural | 1 | 54 | 54 |
| counterexample | 33 | 85 | 85 |
| **total durable** | **222** | **759** | **723** |

These counts matched an independent pre-migration oracle exactly.

## Procedural evidence

The one currently evidence-qualified procedure is:

fuel_starved:no_fuel | resupply:insert_fuel_from_world_container

Observed executed support:

- n=54;
- success=45;
- failure=9;
- mean reward=0.8333333333333334;
- Wilson lower 95% bound=0.7126323220121027.

The confidence value is derived only because these outcomes are binary and executed. Non-binary
procedure rewards retain confidence=None rather than being interpreted as probabilities.

## Promotion invariants

F4-A promotion is fail-closed.

Implementation alone does not promote F4.
Artifact/database alone do not promote F4.
Document alone does not promote F4.

Promotion requires:

- F3 Exit Gate PASS;
- this document;
- valid canonical F4-A artifact;
- clean code revision provenance;
- all SHADOW/no-mutation checks;
- positive episodic/semantic/procedural/counterexample populations;
- bounded non-persistent working memory;
- memory SQLite quick_check=ok;
- schema cortex_cognitive_memory_v1;
- persisted original-batch occurrence count equal to artifact;
- independently recomputed original-batch manifest SHA equal to artifact;
- every artifact check true.

Changing a persisted item digest in the original batch causes validation to fail and the control
plane to fall back from F4 to F3 COMPLETE.

## Tests and gates

Focused F4-A/control-plane/UI gate:

- 44 tests PASS;
- Ruff PASS;
- JavaScript syntax PASS;
- py_compile PASS;
- whitespace PASS.

Full implementation gate:

- 1508 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

## Authority boundary

Canonical migration:

- authority=shadow;
- world_mutation=false;
- factorio_rcon_used=false;
- fle_environment_created=false;
- world_lease_acquired=false;
- execution_grant_created=false;
- continuous_authority=false.

No new Factorio experiment was run.

## Claim boundary

F4-A proves:

- working memory is bounded and distinct from durable memory;
- episodic, semantic, procedural and counterexample memories have typed durable representations;
- provenance is occurrence-level and tamper-evident;
- semantic duplicates can accumulate support without erasing evidence;
- incomplete semantic provenance is separated from qualified support;
- procedural confidence can be conservatively estimated from executed binary outcomes;
- counterexamples are first-class persistent memories;
- the canonical migration is restart-safe and independently auditable.

F4-A does not prove:

- memory improves decisions;
- retrieval quality;
- consolidation quality;
- forgetting/decay policy validity;
- cross-seed transfer;
- statistically significant memory benefit;
- F4 Exit Gate completion.

## Decision

F4-A: PASS parcial.

F4 is active in SHADOW.

Next checkpoint: F4-B — hybrid structural/similarity retrieval + consolidation + decay policy.

F4-B must remain replay/shadow first and must not route memory through curriculum_runner as the
Cortex authority path.

F4 Exit Gate remains unchanged: memory must later show statistically detectable transfer loss when
ablated. Logging or successful retrieval alone is insufficient.
