# Cortex Research — F4-C Causal Memory Ablation + Transfer Protocol

**Phase:** F4 — Cognitive Memory and Consolidation
**Checkpoint:** F4-C — causal memory ablation + held-out transfer
**Status:** **BLOCKED / PRE-REGISTRATION REQUIRED**
**Current validated checkpoint:** F4-B / SHADOW
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
- phase4_blocker.code = causal_transfer_protocol_not_frozen
- resume.do_not_start_another_seed = true

## 8. Decision

**F4-C is not yet experimentally eligible.**

The correct next action is to design and freeze a diverse non-confirmatory held-out transfer
benchmark. Only after that protocol has an immutable manifest may paired MEMORY ON versus MEMORY
ABLATED evaluation begin.

This blocked state is a quality result, not a failure: it prevents a degenerate lookup benchmark
from being promoted into a causal memory claim.
