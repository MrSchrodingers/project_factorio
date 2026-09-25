# Cortex Research — F3-A Executive Shadow Kernel

Phase: F3 — Executive / Cognitive Loop
Checkpoint: F3-A — Executive Shadow Kernel
Decision: PASS parcial de F3
Implementation commit: b54b3a41b797c90f1b0993e4b2eea9526a76bb70
Authority: SHADOW only
Continuous autonomous authority: OFF
Evolution: inactive + disabled
Confirmatory seeds: frozen / untouched

## Scientific purpose

F2 established a generic Option/API execution substrate. F3 changes a different layer: who decides
which alternative should be proposed and why.

F3-A introduces an explicit executive decision kernel without granting live authority. It separates
state, goal, diagnosis, candidate generation, hard feasibility, policy scoring and prediction. The
kernel exposes the complete candidate set before selection instead of hiding choice inside a
stage-coded handler.

## Typed executive contract

Implemented in src/factorio_ai_lab/cortex/executive.py:

- BeliefState;
- ExecutiveGoal;
- immutable GoalStack;
- GoalDiagnosis;
- ExecutiveCandidate;
- CandidateGenerationResult;
- FeasibilityAssessment;
- ChoicePolicy protocol;
- deterministic ScoreTablePolicy baseline;
- ChoiceDecision;
- ExecutiveShadowRun.

Hard feasibility is fail-closed. An explicitly required resource that is unavailable or unknown is
rejected; missing evidence is never promoted to availability.

F3-A imports no live executor, FLE adapter, RCON client, grant ledger or curriculum_runner.
ExecutiveShadowRun is structurally SHADOW-only and reports
world_mutation=false / execute_authorized=false.

## Canonical SHADOW replay

Artifact:

runs/audits/cortex_f3a_executive_shadow_replay.json

SHA-256:

c9b9d2c62a5a4dbd79a1937ae589e8abe5f4efcf6377a047b97f17f961203e65

Run:

cortex-f3a-shadow-20260925T175136Z

Artifact code revision:

b54b3a41b797c90f1b0993e4b2eea9526a76bb70, dirty=false.

Observed evidence comes from the historical repair ledger:

- source: runs/repairs.jsonl;
- source SHA-256:
  327be3b7f7b594449f52ff18d4dcfe7e750a9473a1cf8da8c65893d0bf2ce376;
- observed run: curriculum-20260924T025516Z;
- generation: 96;
- stage: Logistic science;
- symptom:
  producer_output_unprocessed:output_buffered_not_processed;
- observed legacy action:
  placement:place_processing_for_buffered_output;
- executed=false;
- outcome reward=null / verdict=unmeasured.

The observed fact is the symptom and legacy choice. The alternative arm is not presented as a world
observation.

## Counterfactual expansion

The pure repair planner expands the same observed diagnosis into two alternatives:

1. placement:place_processing_for_buffered_output;
2. rebuild:reroute_producer_logistics.

The artifact marks counterfactual_expansion.observed_in_world=false.

Two policies receive the exact same candidate IDs:

- f3a-shadow-prefer-build selects placement;
- f3a-shadow-prefer-reroute selects rebuild.

Therefore candidate generation and choice policy are observably separable. The selected candidate
changes while goal, belief, diagnosis and candidate set remain fixed.

Both candidates carry the predeclared prediction:

- metric: producers_reaching_processor;
- direction: increase.

No action is executed.

## Gate evidence

Focused F3-A/control-plane/UI gate:

- 35 tests PASS;
- Ruff PASS;
- JavaScript syntax PASS;
- whitespace PASS.

Full implementation gate:

- 1478 core/FLE tests PASS;
- 2 PyTorch tests PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

Promotion is fail-closed:

- implementation alone does not promote F3;
- artifact alone does not promote F3;
- F3-A requires F2 Exit Gate PASS + this document + valid canonical artifact + matching
  repairs.jsonl SHA + all SHADOW/no-mutation checks.

## Claim boundary

F3-A proves:

- typed belief and goal frames can drive a decision kernel;
- one observed goal-linked symptom can expose multiple alternatives;
- hard feasibility is explicit and fail-closed;
- policy is separable from candidate generation;
- different policies can make different choices over the same candidate set;
- predictions exist before action.

F3-A does not prove:

- live executive authority;
- sustained autonomy;
- learned-policy superiority;
- correctness of the unexecuted counterfactual outcome;
- verification-after-action;
- credit assignment;
- persistent experiment ledger;
- paired shadow superiority over the legacy runner;
- F3 Exit Gate completion.

## Decision

F3-A: PASS parcial.

F3 is active in SHADOW.

Next checkpoint: F3-B — verification-after-action + credit assignment + persistent experiment
ledger, using measured historical outcomes first. No new live Factorio authority is required for
that checkpoint.
