# Cortex Research — F3-C Paired Shadow Comparison

Phase: F3 — Executive / Cognitive Loop
Checkpoint: F3-C — paired shadow comparison against the legacy decision baseline
Decision: PASS — F3 COMPLETE
Implementation commit: 6a209527a14cc9a8eebb12c4d8ac403884fde281
Authority: SHADOW only
Continuous autonomous authority: OFF
Evolution: inactive + disabled
Confirmatory seeds: frozen / untouched

## Scientific purpose

F3-A made belief, goals, candidates, feasibility, policy and prediction explicit before action.
F3-B made verification, credit assignment and experiment persistence explicit after measured action.

F3-C closes the remaining F3 requirement: compare the Cortex decision surface with the historical
runner decision baseline over the same observed objectives, while keeping candidate generation and
policy selection visible.

This checkpoint measures decision structure, coverage, agreement and divergence. It does not assign
causal performance to an action that was never executed.

## Canonical source population

Source:

runs/repairs.jsonl

Source SHA-256 at replay time:

327be3b7f7b594449f52ff18d4dcfe7e750a9473a1cf8da8c65893d0bf2ce376

The canonical replay selected only historical structural rows with:

- one of two supported material-flow symptoms;
- choice_basis=fixed_rule_no_history;
- executed=false;
- a non-empty legacy action and target set.

Observed population:

- paired episodes: 29;
- producer_output_unprocessed:output_buffered_not_processed: 28;
- producer_chain_reaches_no_sink:chain_reaches_no_sink: 1;
- historical placement choice: 28;
- historical rebuild choice: 1;
- executed=true: 0;
- numeric reward: 0.

Every persisted pair is revalidated against the original JSONL row, including choice_basis.

## Candidate reconstruction

For every historical objective the Executive reconstructs:

- a typed BeliefState;
- one ExecutiveGoal;
- one GoalDiagnosis;
- one candidate set;
- hard-feasibility assessments;
- prediction-before-action;
- policy-scored choice.

Observed candidate sets:

For producer_output_unprocessed:

1. placement:place_processing_for_buffered_output;
2. rebuild:reroute_producer_logistics.

For producer_chain_reaches_no_sink:

1. rebuild:reroute_producer_logistics;
2. placement:connect_producer_to_consumer.

All 29 pairs expose at least two candidates.
Candidate sets are invariant across the compared policies: 29 / 29.

## Canonical no-history baseline

The replay independently recomputes the canonical fixed rule by calling select_action over the
candidate tuple with score=None.

This decision does not read the historical action label.

All 29 historical rows declare choice_basis=fixed_rule_no_history, and the recomputed canonical
fixed rule agrees with the persisted historical choice in 29 / 29 pairs.

Therefore the historical runner choice remains representable as a deterministic baseline over the
same candidate surface without replaying curriculum_runner or a stage handler.

## Policy separation experiment

Two fixed Executive policies are evaluated over the same goal/belief/candidate set.

Legacy-preference score policy:

- legacy action agreement: 29 / 29;
- agreement rate: 1.0.

Rebuild-preference policy:

- agreement with historical action: 1 / 29;
- agreement rate: 0.034482758620689655.

Policy divergence:

- divergent pairs: 28 / 29;
- divergence rate: 0.9655172413793104.

This proves that choice policy is separable from candidate generation and that the same observed
objective can produce a different explicit choice without changing the candidate set.

It does not prove that the rebuild-preference policy is better.

## Canonical artifact

Artifact:

runs/audits/cortex_f3c_paired_shadow_comparison.json

SHA-256:

13b00f67b7014a5f3af3bf068e9381bc563100c4a984688ca88ab23795ba5f01

Run:

cortex-f3c-shadow-20260925T182038Z

Artifact code revision:

6a209527a14cc9a8eebb12c4d8ac403884fde281, dirty=false.

Artifact authority boundary:

- authority=shadow;
- world_mutation=false;
- factorio_rcon_used=false;
- fle_environment_created=false;
- world_lease_acquired=false;
- execution_grant_created=false;
- continuous_authority=false.

No new Factorio experiment was run.

## Stage-handler independence

Static source audit:

- src/factorio_ai_lab/cortex/executive.py imports no curriculum_runner;
- scripts/run_cortex_f3c_shadow_comparison.py imports no curriculum_runner;
- neither imports live Option execution, StructuralTransactionalAdapter, FLE or factorio_rcon.

The historical runner is represented only by persisted decisions and the canonical fixed decision
rule. It is not executed to produce the F3-C choice.

## Tests and gates

Focused F3-C/control-plane/UI gate:

- 32 tests PASS;
- Ruff PASS;
- JavaScript syntax PASS;
- py_compile PASS;
- whitespace PASS.

Full implementation gate on a stable tree:

- 1493 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

Promotion is fail-closed:

- implementation alone does not promote F3-C;
- artifact alone does not promote F3-C;
- document alone does not promote F3-C;
- every paired row must still match its original historical evidence;
- fixed_rule_basis_count must equal paired_episode_count;
- canonical_fixed_rule_agreement must equal paired_episode_count;
- every pair must expose multiple candidates;
- policy divergence must be non-zero;
- all observed actions must remain unexecuted with no observed reward.

## F3 Exit Gate

F3 Exit Gate requirement:

same objective generates alternatives and observable choices; sequence is not embedded in a stage
handler.

Result: PASS.

Evidence chain:

- F3-A: explicit belief/goal/diagnosis/candidates/feasibility/policy/prediction;
- F3-B: verification/credit/persistent experiment ledger;
- F3-C: paired same-objective comparison with observable alternative choices and no stage-handler
  execution.

## Claim boundary

F3 proves an explicit cognitive/executive decision loop and evidence lifecycle.

F3 does not prove:

- learned-policy superiority;
- counterfactual outcome correctness;
- sustained autonomous production;
- live continuous executive authority;
- cross-seed transfer from memory;
- world-model advantage;
- open-play autonomy.

These remain future research questions with separate gates.

## Decision

F3-C: PASS.

F3: COMPLETE.

Next phase: F4 — Cognitive Memory and Consolidation.

F4 must begin without enabling continuous authority. Its Exit Gate requires an ablation result:
removing memory must cause a statistically detectable loss on transfer tasks. Merely storing more
logs does not satisfy F4.
