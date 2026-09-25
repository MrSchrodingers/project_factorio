# Cortex Research — F4-C Causal Memory Ablation + Transfer Protocol

**Phase:** F4 — Cognitive Memory and Consolidation
**Checkpoint:** F4-C — causal memory ablation + held-out transfer
**Status:** **FROZEN / CAUSALLY ELIGIBLE; EVALUATION HARNESS NOT VALIDATED**
**Current validated checkpoint:** F4-B / SHADOW
**F4 Exit Gate:** OPEN; causal evaluation not run
**Continuous autonomous authority:** OFF
**Evolution:** inactive + disabled
**Confirmatory seeds 20261101–20261110:** frozen / untouched

## 1. Scientific question

F4-C must answer a causal question, not a retrieval question:

> Does access to the Cortex cognitive-memory substrate improve performance on transfer tasks that
> were not used to create, tune, select or validate that memory policy?

F4-A proved the durable typed substrate. F4-B proved deterministic hybrid retrieval,
consolidation and non-destructive decay. Neither proves that memory improves downstream decisions.

The F4 Exit Gate remains:

> Removing memory causes a statistically detectable performance loss on held-out transfer tasks.

Until that statement is supported by a frozen paired experiment, F4 remains ACTIVE.

## 2. Why the existing exploratory corpus is not eligible

A read-only audit on 2026-09-25 inspected the five corrected exploratory baseline seeds:

- 20261001
- 20261002
- 20261003
- 20261004
- 20261005

Each seed is independently sandboxed, but the decision surfaces are too homogeneous for an honest
causal memory claim.

### 2.1 Repair surface

Observed repairs.jsonl row counts:

- 20261001: 2 rows
- 20261002: 3 rows
- 20261003: 3 rows
- 20261004: 3 rows
- 20261005: 3 rows

The rows are dominated by only two symptom/action families:

1. fuel_starved:no_fuel -> resupply:insert_fuel_from_world_container
2. producer_output_unprocessed:output_buffered_not_processed
   -> placement:place_processing_for_buffered_output

A deterministic fixed rule already maps those symptoms to those actions. Replaying them as a memory
benchmark would mostly test lookup consistency, not transfer.

### 2.2 Spatial surface

Each corrected exploratory seed contains exactly one spatial demonstration.

Across all five:

- task = belt_route
- start = (19.5, 84.5)
- goal = (24.5, 87.5)
- planner = weighted A*
- route_cost = 8.25
- accepted = true

Path shape varies slightly in one seed, but the task geometry and cost remain effectively fixed.
This is not sufficient task diversity for a causal transfer claim.

### 2.3 Global open-play counterexamples

The global historical corpus contains:

- 85 counterexample rows;
- 85 distinct run ids;
- 33 signatures;
- 52 later rows whose signature had already appeared.

This is useful replay material, but those records are runs, not a preregistered independent
cross-seed transfer experiment. They may support pilot design; they may not substitute for the F4
Exit Gate.

## 3. Required F4-C protocol before any experiment

The protocol must be frozen before evaluation data are observed.

### 3.1 Source/evaluation separation

- source-memory seeds/worlds and evaluation seeds/worlds must be disjoint;
- no event from an evaluation seed may enter memory before its paired evaluation is complete;
- no future row may influence an earlier query;
- confirmatory seeds 20261101–20261110 remain unavailable for protocol tuning.

### 3.2 Paired intervention

Every evaluation task must be run under the same world/checkpoint and code revision in two
conditions:

1. **MEMORY ON** — normal F4-B retrieval is available;
2. **MEMORY ABLATED** — retrieval returns no prior cognitive memory while all non-memory tools,
   feasibility rules, deterministic planners, action surface and runtime budgets remain matched.

The ablation must remove memory access, not remove unrelated capabilities.

### 3.3 Counterbalancing and contamination control

- condition order must be counterbalanced or randomized;
- each pair starts from an equivalent checkpoint;
- no state mutated by the first condition may leak into the second;
- no online learner may update from the first arm before the second arm finishes;
- evaluator/outcome extraction must be identical across arms.

### 3.4 Diversity gate

Before a F4-C evaluation can be called eligible, the non-confirmatory evaluation set must contain
multiple genuinely distinct transfer surfaces, not repeated copies of the same symptom or route.

At minimum the frozen protocol must specify:

- multiple task families;
- multiple independent evaluation seeds/worlds;
- the source-memory seeds/worlds;
- exact eligibility/exclusion rules;
- exact task-generation procedure;
- immutable protocol manifest.

A pilot may be used to estimate variance and choose sample size, but pilot outcomes cannot be reused
as confirmatory F4-C evidence.

## 4. Outcomes and statistics

The primary endpoint must be declared before the evaluation run.

Recommended structure:

- primary paired utility ΔJ = J_memory_on - J_memory_ablated;
- hard task-success/functional-postcondition rate as a co-primary or key secondary metric;
- invalid-action/refusal rate;
- recovery/repair count;
- intervention/manual-logistics burden when instrumented;
- action/tick/computation cost where comparable.

The final frozen protocol must declare:

- the exact formula for J;
- direction of improvement;
- sample size rationale/power or precision target;
- paired statistical test;
- effect size;
- 95% confidence interval;
- multiplicity correction for secondary claims;
- handling of missing/unmeasured outcomes.

Missing or unmeasured values must never be converted to zero merely to complete a table.

For small paired samples, prefer exact/randomization-compatible inference over asymptotic claims.

## 5. Authority and safety constraints

F4-C does **not** require continuous live authority.

During protocol design and pilot preparation:

- evolution remains OFF;
- no scheduler is introduced;
- confirmatory seeds remain untouched;
- no new live Cortex execution is justified solely to make the dashboard look active;
- WORLD live and historical EVIDENCE remain visually and semantically separated.

Any future live arm must use the existing Option/transaction/lease/grant authority boundaries.

## 6. UI semantics at this checkpoint

Dashboard hardening implementation:

0c94090f91ad43c1a3a47a6bec25f377f9e56e00

The UI now distinguishes:

1. **WORLD LIVE** — actual RCON world;
2. **CORTEX CURRENT CONTROL PLANE** — F4-B SHADOW / F4-C protocol work;
3. **HISTORICAL EVIDENCE** — G97, curriculum, UCB, models, generation history and timelines retained
   from earlier baseline/evolution work.

When no research runner is active:

- the dashboard must not label stale research_state.json as active learning;
- G97 is historical/frozen, not evaluating;
- evolution is shown as OFF;
- curriculum/timeline are labeled historical;
- the live map may legitimately contain zero entities;
- zero live entities must be shown as an observed empty world, not as renderer failure.

No physical entity snapshot was persisted for the corrected exploratory seeds, so the dashboard must
not synthesize a fake historical factory and label it live.

## 7. Current machine-readable state contract

Expected after regeneration:

- phase = F4
- phase_status = active
- phase4_checkpoint = F4-B
- phase4_next_checkpoint = F4-C
- phase4_exit_gate.memory_substrate = true
- phase4_exit_gate.hybrid_retrieval_consolidation_decay = true
- phase4_exit_gate.causal_memory_ablation_transfer = false
- phase4_exit_gate.validated = false
- phase4_causal_protocol.validated = true
- phase4_causal_protocol.eligible = true
- phase4_causal_protocol.execution_ready = false
- phase4_blocker.code = causal_transfer_evaluation_harness_not_validated
- resume.do_not_start_another_seed = true

## 8. Decision

**F4-C preregistration is experimentally eligible; execution is not yet ready.**

The correct next action is to implement and validate the paired evaluation harness for the frozen held-out transfer
benchmark. Only after the harness proves checkpoint restore, arm isolation, matched budgets and
outcome extraction may any pilot seed begin.

This blocked state is a quality result, not a failure: it prevents a degenerate lookup benchmark
from being promoted into a causal memory claim.

## 9. Frozen preregistration — 2026-09-25

The qualitative requirements above are now frozen as an executable, machine-validated protocol.
This section supersedes the previous BLOCKED decision only with respect to protocol eligibility.
It does not promote F4-C and does not establish memory benefit.

Implementation lineage:

- executable preregistration: 40b05de883f0f3654ad9de64638a210c30d7f975;
- inferential hardening / frozen implementation:
  fa2dff30ec717b18dc7412c2b1246cbb95e0dce8.

Tracked manifest:

- path: configs/cortex_f4c_causal_ablation_v1.json;
- schema: cortex_f4c_causal_ablation_protocol_v1;
- protocol id: cortex-f4c-memory-ablation-transfer-v1;
- raw file SHA-256:
  82253e71dc94cd5ad803e1340523d4053dfc0a11299848709e6f7dc8414f7c41;
- canonical JSON SHA-256:
  e6633dccc851470a6943ae426b767cb1c039cedb1bede23dc9e6f1c27c94bdac.

Freeze evidence:

- path: runs/audits/cortex_f4c_protocol_freeze.json;
- SHA-256:
  6b818ae63d57062fd4a4f70a0a411f49a1356b053418d6b40d2e90ae95b80c81;
- code revision: fa2dff30ec717b18dc7412c2b1246cbb95e0dce8;
- authority=shadow;
- world_mutation=false;
- no Factorio RCON, FLE environment, WorldLease or execution grant.

### 9.1 Frozen source memory

The F4-C source memory remains the F4-B snapshot for the entire experiment:

- 222 items;
- 759 occurrences;
- DB manifest:
  e6aa69816fe992f6b2a6afc8aff529fa5f1572106ca0939cee830af5a6cf3399;
- F4-A artifact SHA-256:
  f45e31785c17cd6222a57937564036dbdd4976ee1d6376b61f340a9d70066228;
- F4-B artifact SHA-256:
  5fc37b0cee5f121c5ff6b6054fc45f4b0a09bad851e34793958bdd3dc1c5a801.

Evaluation-derived memory writes are quarantined until every held-out evaluation pair is complete.

### 9.2 Frozen populations

Pilot, non-confirmatory:

- seeds 20261201 through 20261208;
- 8 paired tasks;
- 2 tasks per family.

Held-out F4-C evaluation:

- seeds 20261221 through 20261240;
- 20 paired tasks;
- 5 tasks per family.

Reserved confirmatory population:

- seeds 20261101 through 20261110;
- excluded from protocol design, pilot, tuning and sample-size adaptation.

The three seed sets are disjoint by construction.

### 9.3 Frozen transfer families

The deterministic generator cortex_f4c_taskgen_v1 freezes four distinct surfaces:

1. structural_flow_repair;
2. fuel_energy_recovery;
3. spatial_logistics_routing;
4. production_transition_planning.

The manifest stores each complete task specification before any F4-C outcome is observed. No task
contains an expected action, optimal action, winner or solution label.

### 9.4 Intervention and arm isolation

Every task is paired from an equivalent checkpoint:

- MEMORY ON: normal retrieval from the frozen F4-B snapshot;
- MEMORY ABLATED: retrieval returns an empty result.

The ablation removes only cognitive-memory access. Candidate generation, deterministic tools,
feasibility rules, action surface, outcome extractor and runtime budgets remain matched.

Order is explicitly counterbalanced:

- MEMORY ON first: 10 pairs;
- MEMORY ABLATED first: 10 pairs.

Matched per-arm budgets are frozen at:

- 12 decisions;
- 32 actions;
- 18,000 observed game ticks;
- 300 wall-clock seconds;
- 2 LLM calls;
- 4 retrieval queries.

### 9.5 Primary endpoint

Higher is better and J is bounded to [0,1]:

J = 0.55*S + 0.25*G + 0.10*(1-C) + 0.10*(1-I)

where:

- S = 1 only when every hard postcondition succeeds, otherwise 0;
- G = satisfied hard postconditions / total hard postconditions;
- C = 0.5*min(1, action_count/max_actions)
      + 0.5*min(1, observed_game_ticks/max_game_ticks);
- I = invalid_or_refused_actions / proposed_actions.

For an initially unsatisfied task with zero proposed actions, I=1.

If any primary component is missing or unmeasured, the pair is invalid for primary inference.
Missing is never converted to zero.

Primary contrast:

delta_J = J_MEMORY_ON - J_MEMORY_ABLATED.

### 9.6 Frozen inference

The primary statistic is mean paired delta_J.

Preregistered test:

- exact paired sign-flip test under the sharp no-memory-effect label-exchangeability null;
- one-sided alternative delta_J > 0;
- alpha=0.05;
- exhaustive enumeration of all 2^m sign assignments for m valid pairs;
- no asymptotic normal approximation.

Preregistered effect sizes:

- mean delta_J;
- median delta_J;
- paired Cohen dz.

The 95% confidence interval targets a constant additive shift tau and is obtained by inversion of the
two-sided exact paired sign-flip test.

Minimum practically relevant effect:

- delta_J_SESOI = 0.05.

A positive F4 causal-memory result requires all three conditions:

1. one-sided exact p <= 0.05;
2. two-sided 95% CI lower bound > 0;
3. mean delta_J >= 0.05.

Secondary inferential claims, if made, use Holm family-wise alpha=0.05 correction.

Sample size is fixed at 20 evaluation pairs: five independent held-out pairs in each of four
families. No trustworthy prior transfer-effect distribution exists, so no model-based power claim is
made. The design prioritizes exact finite-sample inference, family coverage and an immutable stopping
rule. Any larger design requires a new protocol version before evaluation starts.

### 9.7 Missingness, interruption and technical invalidity

Predeclared technical invalidities are:

- checkpoint restore mismatch;
- code revision mismatch between arms;
- source-memory manifest mismatch;
- cross-arm world-state leakage;
- evaluation-memory contamination;
- outcome extractor failure.

A technically invalid task invalidates the whole pair. The pair is not replaced by a new seed after
outcomes become visible.

A task timeout with valid instrumentation is an observed task failure.

A transport/tool timeout is not an experimental failure by itself. Process, lease, grant, artifact
and world state must be audited first. The same arm may resume only if state equivalence is proven;
otherwise the pair becomes technically invalid.

Outcome-dependent exclusion is forbidden.

The primary analysis requires at least 16 valid pairs overall and at least 3 valid pairs per family.
Below either threshold the experiment is inconclusive, not evidence of a null or negative memory
effect.

### 9.8 Pilot and stopping rule

The 8 pilot pairs may validate instrumentation and estimate variance, but cannot be reused as F4-C
evaluation evidence. Pilot-driven changes require a new protocol version before evaluation.

Once held-out evaluation begins, sample size, task families, endpoint weights, source memory and
stopping rules cannot change from observed outcomes.

### 9.9 Machine-readable state after freeze

The current intended state is:

- phase = F4;
- phase4_checkpoint = F4-B;
- phase4_next_checkpoint = F4-C;
- phase4_causal_protocol.validated = true;
- phase4_causal_protocol.eligible = true;
- phase4_causal_protocol.execution_ready = false;
- phase4_exit_gate.causal_memory_ablation_transfer = false;
- phase4_exit_gate.validated = false;
- phase4_blocker.code = causal_transfer_evaluation_harness_not_validated;
- resume.do_not_start_another_seed = true.

The protocol is eligible. The experiment is not.

### 9.10 Next gate

Before even one F4-C pilot seed can run, the paired evaluation harness must prove:

1. deterministic reconstruction from the frozen manifest;
2. same checkpoint digest before each arm;
3. verified restore between arms;
4. same source-memory manifest in both arms;
5. retrieval-only ablation;
6. matched non-memory tool/action surfaces;
7. matched budget accounting;
8. memory-write quarantine;
9. identical outcome extraction;
10. fail-closed interruption handling;
11. complete provenance for every J component and delta_J;
12. confirmatory seeds untouched and evolution inactive+disabled.

Until the harness is implemented, tested, versioned and projected as execution_ready=true, no pilot
or held-out F4-C seed is authorized.

## 10. Updated decision

F4-C preregistration: PASS / FROZEN.

F4-C causal experiment: NOT RUN.

F4 Exit Gate: OPEN.

The blocker has moved from causal_transfer_protocol_not_frozen to
causal_transfer_evaluation_harness_not_validated without observing F4-C outcomes.
