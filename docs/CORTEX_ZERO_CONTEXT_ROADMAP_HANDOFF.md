# Factorio AI Lab — Cortex Research
# Zero-Context Roadmap + Academic/Operational Handoff

**Purpose:** canonical restart document for a new AI/operator with zero conversational context.
**Repository:** `MrSchrodingers/project_factorio`
**Live checkout:** `/srv/factorio-ai-lab`
**Transition branch:** `research/cortex-v1`
**Current scientific phase:** **F4 ACTIVE — F4-B HYBRID MEMORY RETRIEVAL**
**F3:** COMPLETE / F3-C
**F4:** ACTIVE / F4-B SHADOW
**Continuous autonomous authority:** OFF
**Confirmatory seeds:** untouched / frozen
**SentinelX context:** sxc_4557STHZ — always resume latest revision

> This document is intentionally redundant with the canonical research program, phase documents,
> machine-readable state and SentinelX continuity context. The redundancy is deliberate: a fresh
> chat must be able to reconstruct the scientific and operational state without depending on prior
> conversation memory.

---

# 0. First-turn protocol for a zero-context chat

A fresh chat MUST reconstruct the live state before changing code or launching any experiment.
Conversation memory and dashboard presentation are lower-authority sources.

## 0.1 SentinelX entry point

Host:
- label: kali
- known host id: host_a07c932d2d737c26
- checkout: /srv/factorio-ai-lab
- continuity context: sxc_4557STHZ

First action: resume the latest SentinelX context revision with detail=full. A resumed context restores
knowledge, not authorization; revalidate volatile facts before mutation.

## 0.2 Revalidate Git and concurrency

Inspect branch, local HEAD, origin/research/cortex-v1, dirty/untracked state, recent commits/tags and
active pytest/ruff/Cortex/evolution/curriculum/open-play processes.

Expected published lineage at this handoff:

- F4-B implementation: a4ff558eccd9df0898fef4138a75fa1b55976a8d;
- F4-B closure: 9a23b661c86ebd2eddb37eec2e1f3d8198c8b9c3;
- UI/status epistemic hardening: 0c94090f91ad43c1a3a47a6bec25f377f9e56e00.

Do not overwrite pre-existing concurrent work. If local HEAD/origin/working tree diverge, audit first.

## 0.3 Rebuild the machine-readable phase state

Regenerate runs/cortex_phase_state.json from scripts/cortex_phase_state.py.

Expected scientific state:

- phase=F4;
- phase_status=active;
- phase2_checkpoint=F2-G5;
- phase3_checkpoint=F3-C;
- phase4_checkpoint=F4-B;
- phase4_next_checkpoint=F4-C;
- phase4_exit_gate.memory_substrate=true;
- phase4_exit_gate.hybrid_retrieval_consolidation_decay=true;
- phase4_exit_gate.causal_memory_ablation_transfer=false;
- phase4_exit_gate.validated=false;
- phase4_causal_protocol.validated=true;
- phase4_causal_protocol.eligible=true;
- phase4_causal_protocol.execution_ready=false;
- phase4_blocker.code=causal_transfer_evaluation_harness_not_validated;
- resume.do_not_start_another_seed=true.

Machine-readable state outranks remembered conversation state.

## 0.4 Validate runtime separation

Scientific baseline runtime:
- /srv/factorio-ai-runtime/current/BUILD_INFO.json
- expected frozen commit: 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac.

Dashboard runtime:
- /srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json.

Dashboard/source may advance independently. Dashboard BUILD_INFO should match the latest published
dashboard/status closure, not an old F2 hardcoded SHA.

## 0.5 Validate services and writers

Check dashboard, LLM, evolution active/enabled and active writer/runner processes.

Expected:
- dashboard active;
- evolution inactive+disabled;
- no curriculum_runner/open_play_runner/evolution_loop/Cortex execution process controlling Factorio.

Do not restart evolution automatically.

## 0.6 Validate the three truth surfaces

Query /api/context, /api/world, /api/factory-graph, /api/evolution, /api/research and /ws/live.

Interpretation:
1. WORLD LIVE = current RCON world.
2. CORTEX CURRENT CONTROL PLANE = machine-readable research phase/authority.
3. HISTORICAL EVIDENCE = persisted baseline/evolution/learning artifacts.

The world may be connected with 0 player-force entities. That is a valid observed empty world.
If no research runner is active, G97, curriculum, UCB, models and timelines are historical/frozen.

External dashboard:
http://midasnet.tail106aa2.ts.net:8765/

The UI is a projection; Git + persisted artifacts + phase-state are authority.

## 0.7 Read these documents in this order

1. docs/CORTEX_ZERO_CONTEXT_ROADMAP_HANDOFF.md
2. docs/CORTEX_RESEARCH_PROGRAM.md
3. docs/CORTEX_HANDOFF.md
4. docs/CORTEX_PHASE4_MEMORY_SUBSTRATE.md
5. docs/CORTEX_PHASE4_MEMORY_RETRIEVAL.md
6. docs/CORTEX_PHASE4_CAUSAL_ABLATION_PROTOCOL.md
7. docs/CORTEX_DASHBOARD_EVIDENCE_SCOPE.md
8. docs/CORTEX_CONTINUITY_PROTOCOL.md
9. F3 phase docs if executive-loop lineage must be reconstructed.
10. F2 authority docs only before any future live-execution discussion.

Canonical ready-to-paste prompt:
docs/CORTEX_NEXT_SESSION_PROMPT.md

Only after reconstruction should work continue on the F4-C paired evaluation harness. Do not launch a seed merely
because old research artifacts say learning/evaluating.

---

# 1. Scientific mission

The project is no longer defined as “write enough automation to beat Factorio.”

The scientific question is:

> Can an agent acquire, test, consolidate, transfer and improve engineering strategies in Factorio
> without the programmer explicitly encoding the sequence of actions that constitutes the solution?

Factorio is used as a controlled research environment because it combines:

- partial observability;
- spatial constraints;
- accumulating state;
- causal physical processes;
- discrete and continuous-like resource flows;
- hierarchical production dependencies;
- long-horizon planning;
- recovery/reconfiguration requirements;
- measurable productivity;
- repeatable seeds/worlds;
- explicit failure modes.

Launching a rocket or producing a particular science pack is a capability milestone, not by itself a
measure of cognition.

## 1.1 Main research hypothesis

A hybrid cognitive architecture combining:

- typed primitive actions;
- temporally extended Options;
- deterministic engineering tools;
- learned decision policies;
- causal/relational memory;
- model-based prediction;
- LLM hypothesis/program generation;
- external verification;
- quality-diversity;
- morphology/network optimization;

should show measurable improvement with experience and transfer to unseen worlds under controlled
compute budgets.

The target is improvement in an outcome vector, not a single proxy.

---

# 2. Central architectural diagnosis

The inherited system was sophisticated but architecturally inverted.

The concise diagnosis is:

> **The system had state, objectives and memory. It did not have a policy.**

Approximately 17.7k lines across the inherited curriculum/open-play/evolution runners encoded much
of the action order, placement, connections and transitions.

The old evolutionary layer tuned roughly a small set of scalar parameters around those scripted
paths. That is useful automation and experimental infrastructure, but it is not a cognitive agent
choosing strategy.

The Cortex program therefore changes **authority**, not the existence of deterministic tools.

A*, production DAGs, runtime recipe catalogs, footprints, material ledgers, transaction/rollback,
factory graphs, CP-SAT/MILP, routing, placement and resupply remain legitimate tools.

The research question becomes: **who decides when and why to use them?**

---

# 3. Non-negotiable scientific principles

## P1 — The environment is the authority

LLM text, neural score, heuristic confidence or memory does not prove a physical event occurred.
Factorio/FLE observations and validated instruments do.

## P2 — Missing is not zero

Every measured quantity must preserve epistemic status:

- observed;
- derived;
- estimated;
- missing;
- invalid.

Missing or invalid data must not silently become a numerical default used in fitness, policy or
acceptance gates.

## P3 — No provenance, no credit

Every reward, capability and transition must identify the causal actions/entities responsible.
Inherited production cannot be credited to a challenger.

## P4 — No baseline, no claim

A learned component must compete against the explicit deterministic or rule-based baseline it is
intended to replace.

## P5 — Training does not imply authority

Authority progression:

`offline -> shadow -> proposal -> control-eligible`

Each transition requires evidence.

## P6 — Learning requires a choice

If a stage handler already dictates the exact action sequence, there is no learned decision.

Legacy runners may remain as baselines, fixtures and demonstration generators, not as the Cortex.

## P7 — LLM proposes, evaluator decides

The LLM may generate hypotheses, decompositions, skills or programs. Hard constraints and the
environment determine viability and outcome.

## P8 — Negative results are first-class

If a neural policy loses to A*, a world model fails to improve downstream decisions, or a real
connectome prior loses to a rewired control, the negative result is preserved.

## P9 — Reproducibility before speed

Claims require:

- seed;
- commit;
- environment version;
- configuration;
- artifacts;
- protocol;
- tests;
- decision record.

## P10 — Checkboxes represent evidence

A checkbox is closed only when evidence + tests + commit + decision exist.

---

# 4. Theoretical foundation

The architecture is intentionally hybrid. No single paper is treated as a blueprint.

## 4.1 Factorio Learning Environment

Primary environment reference:

Hopkins, Bakler & Khan, *Factorio Learning Environment* (2025), arXiv:2503.09617.

Role in this project:

- controlled agent-environment interaction;
- transactional action execution;
- repeatable experiments;
- programmatic state extraction;
- explicit world dynamics.

Guardrail:

FLE convenience methods are not automatically scientific truth. Instrumentation must be validated
against Factorio behavior.

## 4.2 Cognitive architectures for agents — CoALA

Sumers et al., *Cognitive Architectures for Language Agents* (2023), arXiv:2309.02427.

The project adopts the separation between:

- working state;
- long-term memory;
- action space;
- decision procedure;
- learning/update mechanisms.

The Cortex state target is:

`B_t = (W_t, G_t, A_t, M_t, U_t, C_t)`

where:

- W = world belief;
- G = goals;
- A = available affordances/actions;
- M = retrieved memory;
- U = uncertainty;
- C = constraints.

## 4.3 Temporal abstraction — Options framework

Sutton, Precup & Singh,
*Between MDPs and semi-MDPs: A framework for temporal abstraction in reinforcement learning*,
Artificial Intelligence 112 (1999), DOI 10.1016/S0004-3702(99)00052-1.

An Option is conceptually:

`o = (I_o, pi_o, beta_o)`

with:

- initiation set;
- internal policy;
- termination condition.

This directly motivates the Cortex transition from tile-level/FLE-level commands to skills such as:

- establish mining cell;
- establish processing chain;
- expand power;
- connect flow;
- rebuild region;
- investigate anomaly;
- research toward capability.

The first concrete Cortex Option is already implemented:

`establish_processing_chain`.

## 4.4 World models — DreamerV3 reference

Hafner et al., *Mastering diverse control tasks through world models*, Nature 640 (2025),
DOI 10.1038/s41586-025-08744-2.

The lesson adopted is not “use Dreamer unchanged.” It is:

> a world model matters when imagined consequences improve future decisions.

Therefore the future Graph World Model cannot gain authority merely by achieving lower predictive
MSE. It must improve downstream action selection on unseen seeds.

## 4.5 Skill accumulation — Voyager

Wang et al., *Voyager* (2023), arXiv:2305.16291.

Useful idea:

- compositional reusable skills;
- open-ended capability growth.

Cortex constraint:

skills are not accepted because an LLM generated convincing code. Skills require typed
preconditions/effects, evaluation, provenance, replay and promotion evidence.

## 4.6 Harness/training separation — Agent Lightning

The program uses Agent Lightning as conceptual support for separating:

- runtime harness;
- trajectories;
- offline learner;
- challenger;
- authority decision.

Target architecture:

`Runtime -> TrajectoryStore -> OfflineLearner -> Challenger -> Shadow -> Proposal -> Authority`

The running harness must not silently mutate itself while it is being evaluated.

## 4.7 Program search — FunSearch and AlphaEvolve

References:

- Romera-Paredes et al., *Mathematical discoveries from program search with large language models*,
  Nature 625 (2024), DOI 10.1038/s41586-023-06924-6.
- Google DeepMind, *AlphaEvolve* (2025).

Adopted principle:

LLMs can propose/mutate executable heuristics, but a deterministic evaluator must control
selection.

Future Cortex skill/program discovery therefore requires:

- sandbox;
- program database;
- evaluator;
- diversity mechanism;
- mutation;
- lint/type/security gates;
- replay;
- holdout promotion.

## 4.8 Quality-diversity — MAP-Elites

Mouret & Clune, *Illuminating search spaces by mapping elites* (2015), arXiv:1504.04909.

Reason for inclusion:

Factorio factory design is multimodal. There may be several useful morphologies rather than one
scalar optimum.

Future descriptors may include:

- autonomy band;
- compactness;
- logistics topology;
- redundancy;
- rebuild cost;
- throughput class;
- energy architecture.

## 4.9 ALNS / ruin-and-recreate

Ropke & Pisinger, Transportation Science 40(4), 2006,
DOI 10.1287/trsc.1050.0135.

Used as methodological grounding for dynamic industrial restructuring:

- destroy operators;
- repair operators;
- adaptive operator weights;
- acceptance criteria;
- rebuild cost/downtime.

The goal is not to hardcode “main bus” or “city block,” but allow the system to discover and revise
morphology.

## 4.10 Statistical rigor / Goodhart guards

Primary methodological references in the program include:

- Henderson et al., *Deep Reinforcement Learning that Matters*;
- Agarwal et al., *Deep Reinforcement Learning at the Edge of the Statistical Precipice*;
- Colas et al., *How Many Random Seeds?*;
- Karwowski et al., *Goodhart's Law in Reinforcement Learning*.

Implications:

- do not report only best seed;
- distinguish exploratory from confirmatory;
- use distributions and effect sizes;
- use IQM/bootstrap CIs where appropriate;
- preserve negative results;
- avoid tuning on confirmatory seeds;
- throughput cannot mask intervention dependence, starvation or inherited output.

## 4.11 Connectomics / NeuroAI

The Cortex-Fly track is explicitly experimental.

References include:

- Dorkenwald et al., *Neuronal wiring diagram of an adult brain*, Nature (2024),
  DOI 10.1038/s41586-024-07558-y;
- Schlegel et al., *Whole-brain annotation and multi-connectome cell typing of Drosophila*,
  Nature (2024), DOI 10.1038/s41586-024-07686-5;
- Jin et al., *FlyGM* (2026), arXiv:2602.17997;
- Wang-Chen et al., *NeuroMechFly v2*, Nature Methods 21 (2024),
  DOI 10.1038/s41592-024-02497-y;
- Scheffer & Meinertzhagen, *A connectome is not enough* (2021),
  DOI 10.1242/jeb.242740.

Epistemic rule:

> a connectome is a structural prior, not a brain simulation and not proof of cognition.

Mandatory controls for the future Fly track:

- real connectome;
- degree-preserving rewired graph;
- random directed graph;
- conventional GNN;
- Graph Transformer;
- matched MLP.

Parameters, data and compute must be matched as closely as possible.

---

# 5. Target Cortex architecture

The target loop is:

```text
OBSERVE
  ↓
BELIEF UPDATE
  ↓
GOAL / BOTTLENECK SELECTION
  ↓
MEMORY RETRIEVAL
  ↓
CANDIDATE HYPOTHESES / OPTIONS
  ↓
HARD FEASIBILITY FILTER
  ↓
COUNTERFACTUAL EVALUATION
  ↓
POLICY / VALUE SELECTION
  ↓
TRANSACTIONAL EXECUTION
  ↓
VERIFY PREDICTION
  ↓
CREDIT ASSIGNMENT
  ↓
EPISODIC WRITE
  ↓
CONSOLIDATION / LEARNING
```

The current project is **not yet at the full loop**.

Current work has concentrated on making the lower half scientifically trustworthy:

- typed actions;
- structural planning;
- functional dependency completion;
- Options;
- transactional execution;
- hard postconditions;
- provenance;
- authority boundaries.

F3 begins the executive decision loop.

---

# 6. Memory model target

## 6.1 Working memory

Short-lived, bounded:

- active goals;
- recent observations;
- bottlenecks;
- current candidates;
- causal hypotheses;
- uncertainty;
- unresolved commitments.

## 6.2 Episodic memory

Canonical causal transition:

`e_t = (s_t, g_t, a_t, predicted_delta, u_t, s_t+1, r_t, c_t)`

Crucial feature:

prediction must exist **before** action, enabling calibration and causal learning.

## 6.3 Semantic memory

A semantic rule must carry more than prose:

- proposition;
- scope;
- evidence;
- support count;
- counterexamples;
- confidence;
- calibration;
- last verified timestamp;
- environment versions;
- causal strength where applicable.

## 6.4 Procedural memory / skills

Skills require:

- preconditions;
- effects;
- cost;
- success distribution;
- known failure modes;
- dependencies;
- provenance;
- examples;
- counterexamples;
- version.

---

# 7. Engineering representation target

The factory should be represented hierarchically:

`World -> Zones -> ProductionCells -> Machines`

and relationally:

`ResourceGraph -> LogisticsGraph -> ProductionGraph -> TechnologyGraph`

The optimization target is multi-objective rather than a single score.

Typical objective dimensions include:

- time;
- intervention;
- area;
- logistics distance weighted by flow;
- congestion;
- WIP;
- energy;
- rebuild cost/downtime;
- expansion headroom;
- robustness;
- quality/throughput.

---

# 8. Roadmap F0–F12

A phase closes only when its Exit Gate is met.

## F0 — Scientific constitution and reproducible baseline

Goal:

define mission, governance, baseline identity and continuity before refactoring.

Status:

**PASS.**

Exit Gate:

an operator with no chat context can identify program state, principles, baseline, phases, next
steps and required evidence from the repository.

Key evidence:

- `docs/CORTEX_RESEARCH_PROGRAM.md`;
- baseline tag `cortex-pre-research-baseline-20260923`;
- initial scientific commits.

## F1 — Instrumentation, isolation and corrected baseline

Goal:

obtain a scientifically valid pre-Cortex baseline.

Status:

**PASS.**

Key achievements:

- corrected factory graph;
- immutable scientific runtime;
- missing/observed/derived semantics;
- dirty-code promotion block;
- seed isolation;
- dashboard evidence scope;
- detached launch/recovery;
- storage hardening;
- statistical baseline report.

Exploratory seeds:

`20261001–20261005`

Result:

5/5 valid exploratory seeds, all partial success, all fail at Logistic science, no green science
output, no closed-loop autonomy.

Confirmatory seeds:

`20261101–20261110`

Status:

**frozen and unused.**

Exit Gate:

reproducible baseline, valid instrumentation, no promotion dependent on unidentified code.

## F2 — Action/Option ontology and universal executor

Goal:

give the agent a real decision/action interface independent of stage scripts.

Status:

**ACTIVE — current phase.**

Original checklist status:

- primitive typed schemas: done;
- ActionRequest/ActionResult/Refusal/EvidenceRef: done;
- facade over deterministic tools: done;
- pre/postconditions: done;
- provenance: done;
- named refusals: done;
- initial Options: done;
- universal transactional execution: still open at durable live level;
- live functional chain through generic Option/API: still open;
- legacy runner enforced as baseline-only: still open.

### F2-A — typed action ontology

PASS.

### F2-B — legacy parity adapters

PASS partial.

### F2-C — generic structural planning

PASS partial.

### F2-D — causal resource identity + prepared structural action

PASS partial.

### F2-E — controlled transactional execution

PASS for transaction semantics; functional counterexample discovered.

Important outcome:

topology could be built, but furnace output remained zero because dependencies were incomplete.

### F2-F — functional dependency completion

Processor fuel/energy and delivery actuator energy became explicit typed dependencies.

### F2-F4C — last accepted live canary

This is the last accepted live Factorio Cortex transaction.

Observed:

- producer reaches processor: 0 -> 1;
- physical processing coverage: 0 -> 1;
- processor exists: false -> true;
- processor output: 0 -> 13 iron plates;
- action accepted;
- transaction committed;
- rollback false.

But final processor status:

`no_fuel`

Therefore:

`functional_accept=true`

`sustained_operation=false`

This is bounded functional success, not sustainable autonomous production.

Canonical artifact:

`runs/audits/cortex_f2f4c_structural_canary.json`

SHA-256:

`2c074e8ec312a5119c59487ee25acae5f1cab7f2ca7e2a2c95cedffada955d37`

### F2-G1 — runner instrumentation independence

PASS partial.

Cortex instrumentation no longer imports the legacy curriculum runner for runtime footprint
measurement.

### F2-G2 — first temporally extended Option

PASS partial.

Implemented:

`establish_processing_chain`

Properties:

- typed initiation/preconditions;
- ordered children;
- functional termination;
- provenance;
- OptionBudget;
- requested vs observed ticks;
- energy sizing based on `max(requested, observed)` when observation exists.

Historical replay remains epistemically honest: old F2-F4C artifacts lack all raw inputs required
for full planner re-execution, so the replay does not fabricate them from current world state.

### F2-G3 — universal Option execution boundary

**PREVIOUS COMPLETED CHECKPOINT.**

Implementation commit:

`e25569db40d6e6186cc24b3c380ebdc9dc4e84cf`

Closure/deploy commit:

`5b3fb7db4c18b804d94701888c8f1aebfe01138b`

Tag:

`cortex-phase2g3-complete-v0.1.0`

Implemented path:

`ProcessingChainOptionPlan -> OptionExecutionBoundary -> StructuralTransactionalAdapter -> TransactionalFLEExecutor`

The boundary validates:

- canonical plan digest;
- Option -> ActionRequest -> Branch -> Prepared lineage;
- code revision and run lineage;
- termination contract;
- explicit authority grant;
- measurement probe;
- tick source before mutation.

SHADOW and PROPOSAL never execute the runtime.

EXECUTE was validated only in deterministic fake/replay.

Accepted fake transaction:

- 600 observed ticks;
- feedback returned to OptionBudget.

Rejected fake transaction:

- rollback restored fake state;
- tick counter rewound;
- result recorded `observed_ticks=null`;
- status `missing_after_rollback`;
- zero was NOT fabricated.

Canonical fake/replay artifact:

`runs/audits/cortex_f2g3_option_execution_fake.json`

SHA-256:

`3a2cdf621e2592766cdec3f5a83ca057b42f3b3c184ce356437a1795a4b97f65`

Critical limitation:

grant consumption is only process-local. Recreating/restarting the boundary loses consumed-grant
state.

Therefore:

**F2-G3 DOES NOT AUTHORIZE LIVE OPTION EXECUTE.**

### F2-G4A — persistent one-shot Option authority

**COMPLETED PREDECESSOR CHECKPOINT.**

Implementation commit:

f567bf453c9e3c0e8dfb319adfeef266b4926af8

Canonical document:

docs/CORTEX_PHASE2_PERSISTENT_OPTION_AUTHORITY.md

Canonical dry-run artifact:

runs/audits/cortex_f2g4a_option_authority_dry_run.json

SHA-256:

94b60b7b8a298252edcb37b4435854c97f83e0f6832de9ec46aec05aff1e1ec6

G4A replaces process-local grant memory with a persistent SQLite authority ledger.

Proven at this checkpoint:

- exact grant id plus Option/prepared-action/plan-digest/code/run binding;
- mandatory non-empty run id;
- issued_at and expires_at;
- exact non-wildcard execution scope;
- max_executions=1;
- duplicate grant id is not silently upserted;
- BEGIN IMMEDIATE plus synchronous=FULL consume path;
- durable consume-before-mutation ordering;
- consumed grant remains refused after process reconstruction;
- expired and mismatched grants fail closed;
- spawned-process double-consume yields exactly one winner;
- dry-run validation does not consume a grant;
- phase-state does not promote from documentation alone.

Dry-run evidence explicitly records factorio_environment_created=false,
factorio_rcon_used=false, factorio_world_mutation=false,
continuous_authority=false, live_option_execute_authorized=false and consumed_at=null.

G4A provides at-most-once authority, not distributed exactly-once world effect. A process crash
after durable consumption and before Factorio mutation leaves the grant consumed and the execution
lost rather than automatically replayed.

Therefore:

**F2-G4A DOES NOT CLAIM LIVE OPTION EXECUTION.**

### F2-G4B — live one-shot Option canary

**CURRENT COMPLETED CHECKPOINT.**

Implementation commit:

794963b435bff616042ca0a6e6f278ead315e5e0

Temporal instrumentation fix:

aaf10beb5b5ec11b7b28e3619823b02b0a465b59

Control-plane integration:

5cbf99dfc739f09c7d9851d27a89c205e97f300a

Canonical live artifact:

runs/audits/cortex_f2g4b_option_live_canary.json

SHA-256:

fb9b69b38a3446dd956ebf529f1888bebfb670cfe59fa8fd8b24b74030f0fc95

Canonical temporal audit:

runs/audits/cortex_f2g4b_temporal_audit.json

SHA-256:

21cdcbe0e60751952600ae1edb26ab4d0d94e72c9fff5d18e3d83f1f023e52d1

The run cortex-f2g4b-20260925T014418Z used seed 424242, outside the frozen confirmatory set.
Exactly one Option execution was attempted, with automatic_retry=false and
continuous_authority=false. The persistent grant was consumed durably before runtime mutation and
was bound to the attested FactorioWorldLease. The Option was accepted, all hard postconditions
passed, physical processing coverage rose 0 -> 1, one producer reached the processor, and 13 iron
plates were produced.

The furnace terminated no_fuel; therefore this is a functional accept, not evidence of sustained
autonomous operation.

The original artifact recorded a tick epoch reset (21840 -> 7800) caused by FLE checkpoint
restoration. The original artifact was not rewritten and no second canary was run. The separate
temporal audit classifies this as functional_accept_tick_epoch_reset_explained.

**DO NOT rerun G4B.**

### F2-G5 — baseline-only enforcement

**CURRENT COMPLETED CHECKPOINT AND F2 CLOSURE.**

Implementation commit:

4fc7217d3e5e0fecb076bfd63305e5498eb52f4e

Canonical audit:

runs/audits/cortex_f2g5_baseline_only_enforcement.json

SHA-256:

f7d796d1c406a7425fd3b14a783227364bb62ab42cef477a59960c41f4d721ae

The legacy curriculum_runner now requires the exact execution role baseline and fails closed before
environment creation for any other role. Baseline launchers label the role explicitly. Static AST
audit finds no curriculum_runner import in the Cortex package. The audit used no RCON and caused no
world mutation.

Full gate: 1467 core/FLE + 2 PyTorch PASS, plus Ruff, compileall, JavaScript, TypeScript/Vite and
whitespace PASS.

**F2 Exit Gate: PASS. F2 COMPLETE.**

F3 is READY / NOT STARTED. Do not infer continuous authority from this transition.

## F3 — Executive / Cognitive Loop

Goal:

generalize the repair loop into goal-directed decision.

Planned components:

- typed BeliefState;
- GoalStack;
- unified deficit/goal diagnosis;
- candidate generator;
- hard feasibility filter;
- choice-policy interface;
- prediction before action;
- verification after action;
- credit assignment;
- experiment ledger;
- shadow comparison versus legacy runner.

Exit Gate:

the same goal generates multiple observable alternatives and choices; the sequence is not embedded
in a stage handler.

Status:

**BLOCKED until F2 exits.**

## F4 — Cognitive memory and consolidation

Planned:

- working memory;
- episodic store;
- semantic store;
- procedural skill library;
- hybrid retrieval;
- counterexamples;
- confidence/support/validity scope;
- consolidation;
- forgetting/decay;
- memory ablation;
- cross-seed transfer.

Exit Gate:

memory removal causes a statistically detectable performance loss in transfer tasks.

## F5 — Learned policy / Agentic RL separation

Planned:

- trajectory schema;
- offline dataset versioning;
- rule baseline;
- contextual bandit where appropriate;
- value/ranking model;
- offline RL challenger;
- shadow/proposal/authority gates;
- regret/calibration;
- harness/trainer separation.

Exit Gate:

learned choice outperforms decision baseline on holdout with CI + ablation.

## F6 — Zoning, morphology and network design

Planned:

- Zones / ProductionCells;
- factory solution objects;
- flow-weighted layout cost;
- congestion-aware routing;
- A* baseline;
- ALNS/LNS;
- adaptive destroy/repair operators;
- rebuild downtime cost;
- expansion headroom;
- Pareto/QD descriptors.

Exit Gate:

system discovers/improves morphology without being given main-bus/city-block as a rule.

## F7 — Graph World Model

Planned:

- graph encoder;
- explicit dynamics;
- residual GNN;
- action-conditioned prediction;
- uncertainty;
- multi-step rollout;
- persistence/GRU/ESN baselines;
- downstream decision evaluation.

Exit Gate:

world model improves future decisions, not merely prediction loss.

## F8 — Skill/program discovery

FunSearch/AlphaEvolve-style track.

Planned:

- program sandbox;
- deterministic evaluator;
- candidate database;
- islands/diversity;
- LLM proposer;
- mutation;
- lint/type/security gates;
- replay before live;
- promotion protocol.

Exit Gate:

at least one newly discovered skill/heuristic beats the explicit baseline on holdout.

## F9 — Cortex-Fly / FlyGM

Planned controls are mandatory.

Exit Gate:

claims about connectome structure are supported or refuted by matched controls.

## F10 — CHE + CFP integration / emergent curriculum

Goal:

common action/evaluator/memory schema and dependency-derived goal frontier.

Exit Gate:

agent progresses in open play without stage script and maintains autonomous production.

## F11 — Perturbation / robustness

Planned:

- resource shocks;
- machine failures where possible;
- power shocks;
- blocked routes;
- demand shifts;
- later biters;
- recovery policy;
- continual-learning forgetting tests.

Exit Gate:

adaptation without full reset or human intervention.

## F12 — Reproducibility and publication

Planned:

- experiment registry;
- immutable manifests;
- dataset/model cards;
- complete ablations;
- statistical scripts;
- reproducible figures;
- limitations;
- negative results;
- replication instructions;
- archival release;
- preprint/paper.

Exit Gate:

a third operator can reproduce the primary claims from a frozen release.

---

# 9. Current live operational state

After G4A publication, reconstruct volatile state from Git, BUILD_INFO, phase-state and services.

## Git / source

Branch:

`research/cortex-v1`

HEAD and origin:

resolve from origin/research/cortex-v1 and the immutable G4A tag; local HEAD must match origin.

Tree:

clean.

GitHub remote:

`https://github.com/MrSchrodingers/project_factorio.git`

## Scientific runtime

Frozen release:

`95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac`

This is intentionally older than the source/dashboard because it is the isolated corrected baseline
runtime.

## Dashboard

Current deployed build at this checkpoint:

resolve from /srv/factorio-ai-dashboard-runtime/current/BUILD_INFO.json and require it to match the published G4A closure SHA.

## Services

Expected:

- factorio-ai-dashboard: active;
- factorio-ai-llm: active;
- factorio-ai-evolution: inactive;
- factorio-ai-evolution enabled state: disabled.

Revalidate every session.

## Factorio live world

The live RCON world left by the last accepted Cortex canary currently contains a small experimental
cell, not the old large baseline factory:

- 1 burner-mining-drill;
- 1 wooden-chest;
- 1 burner-inserter;
- 1 stone-furnace;
- character.

Observed physical graph:

`burner mining drill -> wooden chest -> burner inserter -> stone furnace`

Current observed details at this handoff:

- chest contains iron ore;
- furnace contains 13 iron plates from F2-F4C;
- furnace final status is no_fuel;
- graph has producer reaching processor;
- physical processing coverage = 1.0;
- halt cause currently fuel starvation.

This is expected live-world state.

---

# 10. Dashboard interpretation

The dashboard intentionally mixes several surfaces, but they MUST be interpreted by provenance.

## 10.1 WORLD

Label:

`WORLD · LIVE RCON`

This is current Factorio state.

It can continue ticking after an experiment is finished.

It is not a frozen scientific result.

## 10.2 EVIDENCE

Current baseline label typically points to:

`EVIDENCE · SEED 20261005`

This is frozen sandbox evidence from the final exploratory corrected-baseline seed.

It is historical evidence, not a running Cortex experiment.

## 10.3 CORTEX CURRENT CONTROL PLANE

Current UI/control-plane state:

- F2 COMPLETE remains the validated execution substrate;
- phase2_checkpoint = F2-G5;
- F2 Exit Gate validated;
- F3 COMPLETE;
- phase3_checkpoint = F3-C;
- F3 Exit Gate validated;
- F4 ACTIVE in SHADOW;
- phase4_checkpoint = F4-B;
- F4-A typed cognitive-memory substrate validated;
- F4-B hybrid retrieval/consolidation/decay validated;
- continuous authority OFF;
- evolution inactive+disabled;
- confirmatory seeds frozen.

F4-A canonical memory state:

- working memory capacity=3, bounded/non-persistent;
- episodic: 54 items / 54 occurrences;
- semantic: 134 items / 566 occurrences / 530 qualified;
- procedural: 1 item / 54 executed supports;
- counterexample: 33 items / 85 occurrences;
- total durable memory items: 222;
- canonical batch occurrences: 759;
- memory SQLite quick_check=ok.

F4-B canonical retrieval state:

- artifact:
  runs/audits/cortex_f4b_memory_retrieval.json;
- artifact SHA-256:
  5fc37b0cee5f121c5ff6b6054fc45f4b0a09bad851e34793958bdd3dc1c5a801;
- memory DB manifest before=after:
  e6aa69816fe992f6b2a6afc8aff529fa5f1572106ca0939cee830af5a6cf3399;
- four canonical retrieval queries PASS;
- repeated semantic items=70;
- semantic duplicate support=432;
- repeated counterexample items=10;
- decay is non-destructive and support-protective;
- no live authority.

F4-B does not establish causal memory benefit. Memory ablation and held-out transfer remain open.

The last live Cortex mutation evidence remains F2-G4B. F3/F4-A/F4-B use no live executive authority.

Next checkpoint: F4-C causal memory ablation + transfer benchmark.

## 10.4 F4 dashboard hardening — completed

The ambiguity previously documented here was hardened in implementation
0c94090f91ad43c1a3a47a6bec25f377f9e56e00.

When the Cortex context is global F3/F4 and no research runner is active, the UI enters historical
evidence mode and explicitly separates:

1. WORLD LIVE;
2. CORTEX CURRENT CONTROL PLANE;
3. HISTORICAL EVIDENCE.

In this mode G97 is historical/frozen, curriculum/UCB/model/timeline cards are historical, evolution
is OFF, the top mission comes from F4 phase-state and F4-C paired harness validation is current work.

A connected zero-entity world is labelled WORLD LIVE empty. No complete physical entity snapshot
exists for the corrected exploratory seeds, so the UI must not invent a historical factory scene
and label it live.

Full hardening gate:

- 1521 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff/compileall/JavaScript/TypeScript/Vite/whitespace PASS.

---

# 11. Baseline scientific result

Corrected exploratory baseline:

five seeds:

- 20261001;
- 20261002;
- 20261003;
- 20261004;
- 20261005.

All five:

- valid;
- isolated;
- partial success;
- fail at Logistic science;
- zero validated green-science output;
- closed-loop autonomy false.

Median autonomy score:

0.50.

Confirmatory seeds:

20261101–20261110.

These are frozen and MUST NOT be used for tuning.

They are reserved for future paired pre-Cortex vs Cortex evaluation after the Cortex has a stable
candidate protocol.

---

# 12. Evidence hierarchy

When sources disagree, use this precedence.

## 12.1 Execution/lifecycle authority

1. Git/source tree and deployed BUILD_INFO;
2. experiment manifest/result/artifact;
3. machine-readable phase state;
4. canonical research docs;
5. SentinelX saved context;
6. dashboard/API projection;
7. conversation memory.

## 12.2 Baseline seed continuity

Canonical order:

1. `configs/cortex_baseline_v1.json`;
2. `baseline_runs/<protocol>/<mode>/<seed>/manifest.json`;
3. `result.json` and generation reports;
4. `runs/cortex_phase_state.json`;
5. runtime BUILD_INFO files;
6. docs;
7. UI.

## 12.3 No inferred success

A missing file, timeout, lost stream or missing UI update does not imply experiment failure.

Before retrying any mutation, check:

- process;
- lease;
- artifact;
- manifest;
- result;
- launcher record;
- heartbeat.

---

# 13. F2 closure — G4B live evidence + G5 baseline-only enforcement

F2-G4B provides the bounded live Option evidence; F2-G5 completes the remaining authority-separation requirement. F2 is complete.

## 13.1 G4A authority result

Authority state is now persisted in SQLite rather than Python instance memory. Grants are one-shot,
expiring, exact-scope and bound to the frozen Option lineage.

## 13.2 G4A causal ordering

Required and tested ordering:

validate digest
→ validate lineage
→ validate code/run/scope
→ validate runtime preconditions
→ validate expiry
→ atomically consume grant
→ persist consumption
→ only then allow runtime mutation

The G4A dry-run stops before consume and mutation.

## 13.3 G4A restart/concurrency result

Restart/reconstruction and concurrent double-consume are fail-closed. Spawned-process races admit
exactly one consumer.

## 13.4 G4A evidence

Implementation commit:

f567bf453c9e3c0e8dfb319adfeef266b4926af8

Artifact:

runs/audits/cortex_f2g4a_option_authority_dry_run.json

Artifact SHA-256:

94b60b7b8a298252edcb37b4435854c97f83e0f6832de9ec46aec05aff1e1ec6

Full implementation gate:

- 1440 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript PASS;
- TypeScript/Vite PASS;
- whitespace PASS.

## 13.5 G4A epistemic boundary

G4A proves durable one-shot authority semantics, not live Factorio execution and not sustainable
autonomy.

The consume-before-mutation design is fail-closed. A crash after consume can lose an execution; it
must not reopen authority automatically.

## 13.6 G4B

G4B is separate and may execute at most one explicit non-confirmatory live Option canary using the
persistent grant ledger and an actual WorldLease-bound scope.

No automatic retry, scheduler or continuous authority is allowed.

# 14. F2 Exit Gate

F2 is NOT complete yet.

The original Exit Gate:

> the agent can assemble a functional chain using primitives/options through a generic API without a
> stage-coded path.

The following remain open:

- durable universal transactional execution;
- live functional-chain test through Option/API without curriculum_runner;
- enforcement that legacy runner is baseline-only.

Do not mark F2 complete until all are closed with evidence.

---

# 15. GitHub workflow

GitHub repository:

`MrSchrodingers/project_factorio`

Primary development branch:

`research/cortex-v1`

A fresh chat should verify the remote after local inspection.

Useful GitHub connector operations when available:

- fetch commit by SHA;
- search recent commits in repository;
- compare branch/tag refs;
- inspect CI/combined commit status.

Do not use GitHub state as a substitute for live host state.

## 15.1 Commit discipline

Prefer small scientific checkpoints.

Recommended pattern:

- implementation commit;
- evidence/docs closure commit;
- immutable tag after closure.

Conventional Commit messages in pt-BR are preferred.

Examples:

`feat: adiciona ledger persistente de grants do Cortex`

`docs: encerra checkpoint F2-G4A do Cortex`

Do not mix unrelated maintenance with a scientific phase transition.

## 15.2 Tags

Never move existing scientific tags.

Create a new tag if a closure correction is needed.

---

# 16. Deploy workflow

## 16.1 Dashboard deploy

Use the versioned deployer:

`scripts/deploy_dashboard.sh <clean-sha>`

Target:

`/srv/factorio-ai-dashboard-runtime/releases/<sha>`

Symlink:

`/srv/factorio-ai-dashboard-runtime/current`

Then restart only:

`factorio-ai-dashboard`

Validate:

- BUILD_INFO commit;
- dirty=false;
- service active;
- HTTP 200;
- `/api/context`;
- phase checkpoint;
- expected frontend label.

## 16.2 Scientific runtime

Do not casually move:

`/srv/factorio-ai-runtime/current`

The baseline scientific runtime is intentionally pinned to:

`95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac`

Any change to the scientific runtime under evaluation is a protocol event and requires explicit
documentation.

---

# 17. Testing workflow

Known Python environments:

- `.venv-fle` for core/FLE profile;
- `.venv` for PyTorch/static tooling.

Recent F2-G3 full gate:

- 1421 core/FLE PASS;
- 2 PyTorch PASS;
- Ruff PASS;
- compileall PASS;
- JavaScript syntax PASS;
- TypeScript/Vite build PASS;
- whitespace PASS.

Typical full profile:

```bash
sudo -u ti env PYTHONPATH=src .venv-fle/bin/python -m pytest -q tests   --ignore=tests/test_torch_spatial_policy.py   --ignore=tests/test_torch_world_model.py

sudo -u ti env PYTHONPATH=src .venv/bin/python -m pytest -q   tests/test_torch_spatial_policy.py   tests/test_torch_world_model.py

sudo -u ti .venv/bin/python -m ruff check src tests scripts
python3 -m compileall -q src scripts
node --check src/factorio_ai_lab/dashboard/static/app.js

cd frontend
sudo -u ti npm run build
cd ..

git -c safe.directory=/srv/factorio-ai-lab diff --check
```

Do not run extremely long monolithic commands when connection stability is poor. Prefer short gates
and resumable phases.

---

# 18. SentinelX operational guidance

## Read/search

Prefer:

- `sentinel_read` for known files;
- `sentinel_search` for focused terms;
- `sentinel_exec` for allowed short commands;
- `sentinel_script_run` for controlled multi-command Bash/Python.

Target host:

`kali`

## Long-running finite work

For jobs likely to exceed connection limits:

- use SentinelX background mode for finite jobs;
- record artifact/log paths before launch;
- do not aggressively poll;
- on reconnect, inspect job/process/artifact before retry.

Never infer failure from a lost chat stream.

## Context continuity

Context:

`sxc_4557STHZ`

At this handoff:

resume the latest revision; revision numbers are operational state, not a document constant.

Use:

`sentinel_context(operation="resume", context_id="sxc_4557STHZ", options={"detail":"full"})`

After a meaningful checkpoint:

save a new revision with concise operational state.

Do not store secrets.

---

# 19. Known operational caveats

- SentinelX agent historically required shell/script for some edits because file operations were
  read-only.
- Git operations should run as `sudo -u ti`.
- `git -c safe.directory=/srv/factorio-ai-lab` is required.
- Avoid overwriting pre-existing dirty work.
- Evolution is intentionally OFF.
- The Factorio world is shared; use WorldLease protections.
- A concurrent canary attempt was previously prevented correctly by WorldLease.
- The live world may keep ticking after an experiment closes.
- FLE post-evaluation operations can advance time; measure actual game ticks rather than assuming a
  wall-clock settle duration.
- Missing/unknown measurements must remain missing/unknown.
- Dashboard historical baseline cards are not proof of active learning/evolution.
- Local Qwen is a hypothesis/program helper, not the scientific authority.

---

# 20. Current live UI interpretation

The dashboard is an observability surface, not authority.

Expected current projection after the F4 status hardening:

- CORTEX SHADOW / F4-B;
- F4-C shown as the next causal-memory checkpoint;
- no active Cortex executor;
- evolution OFF;
- G97/evolution/curriculum/UCB/model/timeline artifacts labelled HISTÓRICO/FROZEN;
- F2-G4B retained as the last live Cortex mutation evidence;
- WORLD LIVE independently reports current RCON state.

If WORLD LIVE has 0 entities:

- this is a measured empty player-force world;
- the map must say Factorio live / empty world;
- this does not imply the dashboard is stuck;
- this also does not imply the Cortex is autonomously running.

The system is intentionally SHADOW/idle while F4-C is preregistered. Starting a runner just to make
the UI look active would violate the research protocol.

Operational verification still checks dashboard BUILD_INFO, service/restart state, /api/context,
/api/world, /ws/live, evolution active/enabled and active writer processes.

Historical cards remain useful evidence, but never prove current control.

---

# 21. Research questions

The program should remain organized around falsifiable research questions.

Representative questions:

**RQ1.** Does replacing stage-coded action order with typed Options improve transfer and reduce
intervention on unseen seeds?

**RQ2.** Does causal episodic/semantic/procedural memory improve transfer beyond logging-only
baselines?

**RQ3.** Does a learned choice policy outperform rule/heuristic selection under matched
observations and tools?

**RQ4.** Does a Graph World Model improve downstream decisions compared with persistence, GRU/ESN
or explicit dynamics alone?

**RQ5.** Does quality-diversity preserve useful factory morphologies that scalar optimization loses?

**RQ6.** Can LLM-driven program search discover heuristics that outperform deterministic/human
baselines on holdout?

**RQ7.** Can ALNS learn useful rebuild/destroy strategies under downtime and flow constraints?

**RQ8.** Does a real Drosophila connectome topology provide benefit over matched rewired/random/GNN/
Transformer/MLP controls?

**RQ9.** Does the integrated system improve sustainable zero-intervention production on unseen
worlds?

---

# 22. Experimental design rules for future claims

## Exploratory vs confirmatory

Never tune on confirmatory seeds.

Confirmatory seeds are frozen:

20261101–20261110.

## Paired comparisons

When possible, use the same seeds/worlds for baseline and Cortex.

## Report distributions

Do not report only mean or best run.

Prefer:

- individual seed results;
- median;
- IQM when suitable;
- bootstrap confidence interval;
- effect size;
- explicit failures;
- intervention rate;
- sustainability.

## Goodhart guard

Do not allow:

- throughput to hide intervention;
- inherited production to count as challenger output;
- stage completion to substitute for physical holdout;
- missing values to become zero;
- temporary production to become “sustainable autonomy.”

## Primary operational metric

The strongest target metric is:

**sustainable autonomous production during a zero-intervention holdout window.**

---

# 23. What is proven vs not proven today

## Proven

- corrected baseline can be isolated and reproduced;
- world/evidence scopes can be separated;
- typed action ontology works;
- structural planning can be performed generically;
- fuel/energy dependencies can be measured and composed;
- a bounded live chain produced real output;
- transaction acceptance/rollback works;
- first processing-chain Option exists;
- Option has explicit temporal budget semantics;
- generic Option execution boundary works in deterministic fake/replay;
- rollback tick rewind is not misclassified as zero observed duration;
- persistent one-shot grant authority survives restart/reconstruction in dry-run;
- double-consume is fail-closed across independent connections and spawned processes;
- exact expiry/run/code/digest/prepared-action/scope mismatches are refused before mutation.

## Not proven

- live execution through the persistent Option authority path;
- live Option-controlled processing chain;
- sustainable autonomous operation;
- closed-loop Cortex executive;
- memory-caused transfer gain;
- learned policy superiority;
- world-model downstream improvement;
- automatic morphology discovery;
- program-search superiority;
- connectome benefit;
- integrated zero-intervention open play;
- distributed exactly-once world effect across a post-consume process crash.

Do not phrase these as completed capabilities.

---

# 24. Files that matter most for F2-G4B

Read before any live execution:

- src/factorio_ai_lab/cortex/grant_ledger.py
- src/factorio_ai_lab/cortex/option_execute.py
- src/factorio_ai_lab/cortex/options.py
- src/factorio_ai_lab/cortex/structural_execute.py
- src/factorio_ai_lab/integrations/fle.py
- src/factorio_ai_lab/runtime.py
- scripts/run_cortex_option_authority_dry_run.py
- scripts/run_cortex_structural_canary.py
- tests/test_cortex_option_grant_ledger.py
- tests/test_cortex_option_execute.py
- docs/CORTEX_PHASE2_PERSISTENT_OPTION_AUTHORITY.md
- docs/CORTEX_CONTINUITY_PROTOCOL.md

Do not start by editing curriculum_runner.py.

G4B must remain on the generic Cortex Option boundary.

---

# 25. Historical F2-G4B validation sequence — reference only

1. Revalidate Git, tag, phase-state, dashboard BUILD_INFO and services.
2. Verify G4A artifact/hash and persistent ledger schema.
3. Confirm confirmatory seeds remain untouched.
4. Confirm WorldLease has no active writer.
5. Define one non-confirmatory canary run id and exact world/lease scope.
6. Build the Option plan without mutation.
7. Freeze exact plan digest and lineage.
8. Issue exactly one persistent grant with short expiry.
9. Acquire the real FactorioWorldLease for that run.
10. Revalidate grant, scope, tick source and runtime preconditions.
11. Atomically consume the grant.
12. Execute exactly once through OptionExecutionBoundary.
13. Preserve existing transactional commit/rollback and hard functional postconditions.
14. Persist tick/provenance/result artifact.
15. On timeout, inspect ledger/lease/process/artifact/result before any retry; default is no retry.
16. Verify evolution remains inactive/disabled.
17. Run focused and full gates from clean versioned code.
18. Update phase-state/docs/UI only from persisted evidence.
19. Commit, push, tag and deploy only if the canary evidence is coherent.

---

# 26. Historical F2-G4B prohibited shortcuts — still-valid authority constraints

Do NOT:

- use a confirmatory seed;
- enable evolution;
- add a scheduler;
- add reusable or wildcard grants;
- issue a second grant because a remote call timed out;
- reuse a consumed grant after restart;
- execute without an actual WorldLease-bound scope;
- weaken F2-F4C functional hard gates;
- bypass StructuralTransactionalAdapter / TransactionalFLEExecutor;
- hardcode a stage-specific path into the Option runner;
- derive authority from UI state;
- silently migrate missing measurements to zero;
- claim exactly-once world effect from SQLite one-shot authority;
- mark F2 complete from one live canary alone.

---

# 27. Publication/versioning checklist per checkpoint

Before declaring a subphase PASS:

- [ ] Evidence artifact exists.
- [ ] Artifact provenance references exact implementation SHA.
- [ ] Focused tests pass.
- [ ] Full core/FLE gate passes.
- [ ] PyTorch gate passes.
- [ ] Static checks pass.
- [ ] Frontend build passes if UI changed.
- [ ] phase_state reconstructs the intended checkpoint.
- [ ] README updated.
- [ ] CORTEX_RESEARCH_PROGRAM updated.
- [ ] CORTEX_HANDOFF updated.
- [ ] working tree clean after commit.
- [ ] branch pushed.
- [ ] local HEAD == origin branch.
- [ ] immutable tag created/pushed.
- [ ] dashboard deployed if observability changed.
- [ ] /api/context verifies deployed checkpoint.
- [ ] evolution state revalidated.
- [ ] SentinelX context saved.

---

# 28. GitHub verification after push

After pushing, verify independently using GitHub connector if available.

Recommended checks:

- fetch final commit SHA;
- confirm commit message and repository;
- inspect branch/ref state;
- inspect CI status if configured;
- compare previous checkpoint tag to current head if needed.

The GitHub remote is the published source of code history; SentinelX is the live operational source.

Both should agree before a checkpoint is considered closed.

---

# 29. Academic publication trajectory

The project is being engineered toward a publishable experimental program, not only a product demo.

A credible paper/preprint path requires:

1. precise architecture description;
2. corrected baseline;
3. explicit interventions;
4. immutable datasets/artifacts;
5. ablation studies;
6. matched baselines;
7. paired seeds;
8. confidence intervals/effect sizes;
9. negative results;
10. limitations;
11. replication instructions;
12. frozen release.

The strongest eventual contribution would not be “AI plays Factorio.”

A stronger claim would be one of:

- a cognitive architecture learns engineering strategy with transfer;
- typed memory/Options measurably improve long-horizon autonomous production;
- a relational world model improves engineering decisions;
- quality-diversity/program search discovers novel factory strategies;
- connectomic topology provides or fails to provide measurable control priors under matched
  controls.

Each must be independently falsifiable.

---

# 30. Final zero-context state summary

At this checkpoint:

- F0 PASS;
- F1 PASS;
- F2 COMPLETE / F2-G5;
- F3 COMPLETE / F3-C;
- F4 ACTIVE / F4-B SHADOW;
- F4-A typed cognitive-memory substrate PASS;
- F4-B hybrid retrieval/consolidation/non-destructive decay PASS;
- F4 Exit Gate OPEN;
- F4-C next checkpoint, currently BLOCKED/PRE-REGISTRATION;
- F4-C preregistration = frozen/eligible;
- execution_ready = false;
- blocker = causal_transfer_evaluation_harness_not_validated;
- continuous autonomous authority OFF;
- evolution inactive+disabled;
- confirmatory seeds 20261101–20261110 untouched/pending;
- resume.do_not_start_another_seed=true;
- last live Cortex mutation evidence = F2-G4B; do not repeat it;
- frozen baseline runtime = 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac;
- F4-B artifact SHA-256 =
  5fc37b0cee5f121c5ff6b6054fc45f4b0a09bad851e34793958bdd3dc1c5a801;
- F4-B DB = 222 durable items / 759 occurrences / quick_check=ok;
- UI/status hardening implementation =
  0c94090f91ad43c1a3a47a6bec25f377f9e56e00;
- hardening gate = 1521 core/FLE + 2 PyTorch PASS plus static/build gates;
- current WORLD may legitimately be connected with 0 entities;
- stale G97/curriculum/UCB/model metrics are historical when no runner is active.

F4-C readiness audit:

- five corrected exploratory seeds exist but are too homogeneous for the causal Exit Gate;
- repairs are dominated by two fixed symptom/action families;
- each seed has one same-geometry weighted-A* demonstration at route_cost=8.25;
- global 85 counterexample runs / 33 signatures are useful for pilot design but are not 85
  independent evaluation seeds.

A fresh operator should leave its first turn with one conclusion:

> F4-B is closed and reproducible. F4 is intentionally still active because causal memory benefit
> has not been established. The next legitimate work is to freeze a diverse non-confirmatory F4-C
> MEMORY ON versus MEMORY ABLATED held-out protocol. No evolution, confirmatory tuning or live
> activity should be started merely to make the dashboard move.

---

# 31. Immutable handoff anchor

The F4-B closure is discoverable through:

- cortex-phase4b-complete-v0.1.0
- cortex-zero-context-handoff-v0.7.0

This update should be published as cortex-zero-context-handoff-v0.8.0 if that tag is still absent.
A fresh chat must still resolve the latest branch HEAD, tags and dashboard BUILD_INFO.

---

# 32. Ready-to-paste bootstrap prompt for a brand-new chat

The canonical prompt is maintained separately:

docs/CORTEX_NEXT_SESSION_PROMPT.md

Use that document as the copy/paste prompt. Its required behavior is:

- reconstruct live state from SentinelX/Git/artifacts first;
- distinguish WORLD LIVE, CORTEX CURRENT and HISTORICAL EVIDENCE;
- verify F4-B evidence and F4-C blocker;
- prepare/preregister F4-C if state is consistent;
- do not launch evaluation seeds until protocol is frozen and eligible;
- do not use confirmatory seeds for tuning;
- do not enable evolution or continuous authority.

## 33. F4-C frozen preregistration update — 2026-09-25

The F4-C protocol-design blocker has been resolved without running any F4-C outcome-producing seed.

Frozen implementation:

fa2dff30ec717b18dc7412c2b1246cbb95e0dce8

Tracked manifest:

configs/cortex_f4c_causal_ablation_v1.json

Raw manifest SHA-256:

82253e71dc94cd5ad803e1340523d4053dfc0a11299848709e6f7dc8414f7c41

Canonical manifest SHA-256:

e6633dccc851470a6943ae426b767cb1c039cedb1bede23dc9e6f1c27c94bdac

Freeze audit SHA-256:

6b818ae63d57062fd4a4f70a0a411f49a1356b053418d6b40d2e90ae95b80c81

Frozen design:

- 4 transfer families;
- 8 non-confirmatory pilot pairs, seeds 20261201–20261208;
- 20 held-out evaluation pairs, seeds 20261221–20261240;
- confirmatory seeds 20261101–20261110 remain untouched and excluded;
- MEMORY ON versus retrieval-only MEMORY ABLATED;
- matched non-memory tools, feasibility, action surface and budgets;
- explicit 10/10 arm-order counterbalancing;
- source memory frozen at the F4-B 222-item / 759-occurrence snapshot;
- evaluation-derived writes quarantined;
- primary endpoint J frozen before outcomes;
- primary contrast delta_J;
- SESOI delta_J=0.05;
- exact paired sign-flip inference and inverted two-sided 95% CI;
- minimum analyzable set 16 pairs overall and 3/family;
- technical invalidity and interruption rules predeclared;
- missing is never zero.

Machine state after freeze:

- phase=F4;
- phase4_checkpoint=F4-B;
- phase4_next_checkpoint=F4-C;
- phase4_causal_protocol.validated=true;
- phase4_causal_protocol.eligible=true;
- phase4_causal_protocol.execution_ready=false;
- phase4_exit_gate.causal_memory_ablation_transfer=false;
- phase4_exit_gate.validated=false;
- blocker=causal_transfer_evaluation_harness_not_validated;
- resume.do_not_start_another_seed=true.

Therefore the next legitimate work is no longer protocol design. It is implementation and validation
of the paired evaluation harness: checkpoint digest/restore, arm isolation, memory-manifest pinning,
retrieval-only ablation, matched budgets, memory-write quarantine, identical outcome extraction and
fully recomputable J/delta_J artifacts.

No pilot seed is authorized until that harness is versioned, tested and machine-gated as
execution_ready=true. F4 remains ACTIVE and no causal memory-benefit claim has been made.
