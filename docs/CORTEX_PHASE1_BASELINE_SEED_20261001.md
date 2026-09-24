# Cortex Baseline Exploratory — Seed 20261001

**Protocol:** cortex_baseline_protocol_v1
**Mode:** exploratory
**Seed:** 20261001
**Verdict:** VALID BASELINE OBSERVATION
**Release under test:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Run:** curriculum-20260924T043932Z

## 1. Experimental validity

The run is valid rather than infrastructure-invalid.

Evidence:

- manifest status: completed;
- returncode: 0;
- result code revision equals the manifest release;
- code revision source: BUILD_INFO;
- dirty: false;
- measurement protocol: cell_attributed_v3;
- unclassified rate keys: none;
- global selection and memory hashes unchanged;
- global evolution_champion.json remained absent;
- no concurrent evolution service;
- Factorio world lease remained scoped to the seed sandbox.

The seed therefore remains in the exploratory sample even though the agent did not complete
Logistic science.

## 2. Outcome

The curriculum completed 14 stages and stopped at Logistic science.

Completed capabilities:

- iron_backbone;
- coal_mining;
- copper_mining;
- copper_smelting;
- steam_power;
- assembler_gears;
- automation_science;
- electronic_circuits.

Final selection decision:

    promoted = false
    reason = no champion selected: challenger failed baseline survival gates

Regressions:

- physical processing coverage below 50%: 33.3%;
- one failed validation stage: Logistic science.

## 3. Primary measurements

| Metric | Seed 20261001 |
|---|---:|
| autonomy_score | 0.5 |
| closed_loop_autonomy | false |
| physical_processing_coverage | 0.333333 |
| manual_logistics_calls | 53 |
| fuel_starved_entities | 0 |
| power_starved_entities | 0 |
| external_dependencies | 0 |
| endogenous_rate_per_s | 0.606284 |
| intervention_rate_per_s | 0.646606 |
| total_rate_per_s | 1.252890 |
| productive_runtime_s | 680.217 |
| route_cost | 8.25 |
| route_turns | 1 |

The intervention rate remains slightly greater than the endogenous rate. This is evidence that
the inherited controller is not yet a self-sustaining autonomous factory controller even though
fuel and power starvation were repaired by the end of the run.

## 4. Early production evidence

The initial iron cell produced:

    34 units / 136 game seconds = 0.25 / s

After scale mining:

    68 units / 134 game seconds = 0.5074626866 / s

The placement learner evaluated eight trials and selected east_near. This is a measurable
improvement in the iron extraction cell, but it does not imply global factory autonomy.

## 5. Logistic-science failure anatomy

The final logistic-science measurement was:

    logistic_science_output = 0
    logistic_science_rate_per_s = 0

The physical factory graph reported:

    producer_count = 6
    producers_reaching_buffer = 5
    producers_reaching_processor = 2
    physical_processing_coverage = 2 / 6 = 33.3%

The critical failure is therefore not merely energy starvation. Four producer paths were not
connected into processing.

### 5.1 Ore distribution

The logistic-science repair state showed ore_distribution status nothing_to_link.

The target stone furnace was rejected because:

    lane_longer_than_the_belt_budget

Several other material links were also refused for the same reason.

This is direct evidence of a global-layout weakness: local cell construction succeeds, but the
controller does not maintain an industrial topology whose distances remain compatible with its
logistics budget.

### 5.2 Repair behavior

The repair system detected two symptoms.

First:

    fuel_starved:no_fuel

Action:

    resupply:insert_fuel_from_world_container

Result:

    before = 5 fuel-starved entities
    after = 0
    verdict = held

This repair succeeded.

Second:

    producer_output_unprocessed:output_buffered_not_processed

Proposed action:

    placement:place_processing_for_buffered_output

Result:

    executed = false
    not_executed = no_runner_binding_for_intent
    verdict = unmeasured

This is a core limitation of the inherited mechanistic architecture. The diagnostic layer can
identify the structural problem and propose the correct class of intervention, but the runner
does not expose a binding capable of executing that repair.

## 6. Interpretation

This failure is retained as baseline behavior. It is not repaired before seed 20261002 because
doing so would change the system under test inside cortex_baseline_protocol_v1.

The observation supports the Cortex research hypothesis being tested:

- the system has competent local tools;
- it can detect physical/logistical failure;
- it can perform some deterministic repairs;
- but authority remains fragmented across handwritten runner bindings;
- it does not yet reason over the factory as a persistent global industrial design problem.

The fact that fuel starvation reached zero while physical processing coverage remained 33.3%
is particularly useful: simply improving resource availability is insufficient. The remaining
problem is topology and action authority.

## 7. Isolation evidence

Before completion, SHA-256 hashes were captured for global evolution history, loop history,
open-play history, knowledge, counterexamples, repairs and the baseline-reset record.

After completion all hashes were unchanged, and the global champion remained absent.

Result:

    ISOLATION = PASS

This empirically validates STATE_ROOT isolation for the first real seed.

## 8. Decision

Seed 20261001 is accepted into the exploratory baseline.

Seed 20261002 is authorized under the same frozen release and protocol.

No code change affecting gameplay, planning, learning, repair or measurement may be deployed
before the exploratory series is complete. Analysis/documentation tooling may evolve in the
source checkout because the runtime remains pinned to release 95c34a53.
