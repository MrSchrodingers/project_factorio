# Cortex Baseline Exploratory — Seed 20261002

**Protocol:** cortex_baseline_protocol_v1
**Mode:** exploratory
**Seed:** 20261002
**Verdict:** VALID BASELINE OBSERVATION
**Release under test:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Run:** curriculum-20260924T052850Z

## 1. Experimental validity

The run is valid rather than infrastructure-invalid.

Evidence:

- manifest status: completed;
- returncode: 0;
- result code revision equals manifest release;
- result code revision dirty=false;
- measurement protocol: cell_attributed_v3;
- unclassified rate keys: none;
- global selection/memory hashes unchanged;
- global evolution_champion.json remained absent;
- factorio-ai-evolution remained inactive;
- dashboard context automatically followed seed 20261002 without changing the scientific runtime.

Result:

    VALIDITY = PASS
    ISOLATION = PASS

## 2. Outcome

The curriculum completed 14 stages and stopped at Logistic science, reproducing the same frontier
as seed 20261001.

Final selection:

    promoted = false
    reason = no champion selected: challenger failed baseline survival gates

Regressions:

- fuel starvation remains after coal capability: 1 entity;
- one failed validation stage: Logistic science.

## 3. Primary measurements

| Metric | Seed 20261002 |
|---|---:|
| autonomy_score | 0.375 |
| closed_loop_autonomy | false |
| physical_processing_coverage | 0.500000 |
| manual_logistics_calls | 53 |
| fuel_starved_entities | 1 |
| power_starved_entities | 0 |
| external_dependencies | 0 |
| endogenous_rate_per_s | 0.606284 |
| intervention_rate_per_s | 0.647644 |
| total_rate_per_s | 1.253928 |
| productive_runtime_s | 620.217 |
| route_cost | 8.25 |
| route_turns | 1 |
| logistic_science_output | 0 |

## 4. Physical graph

Final graph:

    node_count = 124
    edge_count = 161
    producer_count = 6
    producers_reaching_buffer = 5
    producers_reaching_processor = 3
    physical_processing_coverage = 50%
    fuel_starved_entities = 1
    power_starved_entities = 0
    steam_path_live = true

Compared with seed 20261001, one additional producer reached a processor. This improved physical
processing coverage from 33.3% to 50%, but did not create logistic science output or closed-loop
autonomy.

This is important: passing a 50% topology threshold is not sufficient to establish the missing
industrial DAG.

## 5. Logistic-science repair anatomy

The final repair sequence first observed six fuel-starved entities.

Action:

    resupply:insert_fuel_from_world_container

Measured:

    drawn = 59
    inserted = 60

Outcome:

    fuel_starved_entities: 6 -> 1
    verdict = held

The repair was directionally correct but exhausted accessible fuel before eliminating starvation.

A second fuel repair could not execute:

    not_executed = world_and_agent_hold_no_fuel

The structural symptom repeated from seed 20261001:

    producer_output_unprocessed:output_buffered_not_processed

Proposed action:

    placement:place_processing_for_buffered_output

Result:

    executed = false
    not_executed = no_runner_binding_for_intent

Thus both independent seeds have independently reached the same action-authority boundary.

## 6. Logistics geometry

The ore-distribution planner built one new link and found several refused candidates.

Observed refusal classes include:

    lane_longer_than_the_belt_budget
    something_already_fills_it

The run did improve connectivity, reaching three of six producers, but the topology remains a
collection of local cells with long or incompatible transfer paths rather than a globally planned
industrial layout.

## 7. Placement variation

The placement learner selected:

    west_near

Seed 20261001 selected east_near.

Both produced the same scaled iron throughput:

    68 units / 134 game seconds = 0.5074626866 / s

This is useful evidence that local placement variants can be throughput-equivalent while their
global geometry differs. A future Cortex should evaluate placement by downstream network cost,
not only local cell throughput.

## 8. Two-seed aggregate

After seeds 20261001 and 20261002:

- valid seeds: 2/2;
- closed-loop successes: 0/2;
- Logistic science failures: 2/2;
- manual logistics calls: 53 in both seeds;
- endogenous rate: 0.60628371/s in both seeds;
- autonomy score median: 0.4375;
- physical-processing coverage median: 41.67%;
- fuel-starved entities median: 0.5;
- power-starved entities: 0 in both seeds;
- external dependencies: 0 in both seeds.

At n=2 this remains exploratory evidence, not a statistical conclusion. However, repeated failure
at the same frontier with the same missing runner binding is stronger evidence of a structural
limitation than the first seed alone.

## 9. Decision

Seed 20261002 is accepted into the exploratory baseline.

Seed 20261003 is authorized under the same frozen scientific release and protocol.

No gameplay, planning, repair, measurement or learning code may be changed before completion of
the five exploratory seeds.
