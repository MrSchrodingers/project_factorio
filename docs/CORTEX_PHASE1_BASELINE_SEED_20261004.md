# Cortex Baseline Exploratory — Seed 20261004

**Protocol:** cortex_baseline_protocol_v1
**Mode:** exploratory
**Seed:** 20261004
**Verdict:** VALID BASELINE OBSERVATION
**Release under test:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Run:** curriculum-20260924T142549Z

## 1. Experimental validity

The run is valid.

- manifest status: completed;
- returncode: 0;
- code revision equals the frozen scientific release;
- dirty: false;
- measurement protocol: cell_attributed_v3;
- unclassified rate keys: empty;
- global isolation hashes unchanged;
- global champion remained absent;
- factorio-ai-evolution remained inactive.

VALIDITY = PASS
ISOLATION = PASS

## 2. Outcome

The curriculum completed 14 stages and stopped at Logistic science.

- bottleneck: Logistic science;
- logistic_science_output = 0;
- logistic_science_rate_per_s = 0;
- closed_loop_autonomy = false.

This is the fourth independent seed to reach the same engineering frontier.

## 3. Primary measurements

| Metric | Seed 20261004 |
|---|---:|
| autonomy_score | 0.5 |
| closed_loop_autonomy | false |
| physical_processing_coverage | 0.5 |
| manual_logistics_calls | 55 |
| fuel_starved_entities | 0 |
| power_starved_entities | 0 |
| external_dependencies | 0 |
| endogenous_rate_per_s | 0.60628371 |
| intervention_rate_per_s | 0.64789673 |
| total_rate_per_s | 1.25418044 |
| productive_runtime_s | 680.2167 |
| route_cost | 8.25 |
| route_turns | 1 |

## 4. Repair anatomy

At Logistic science, the repair system observed fuel starvation and structural processing failure.

Fuel repair:

    symptom = fuel_starved:no_fuel
    before = 6
    after = 0
    verdict = held

A second fuel repair also held and left final fuel starvation at zero.

Structural repair:

    symptom = producer_output_unprocessed:output_buffered_not_processed
    intent = place_processing_for_buffered_output
    executed = false
    not_executed = no_runner_binding_for_intent

This reproduces the central architectural limitation independently of fuel availability.

## 5. Interpretation

Seed 20261004 is especially informative because final fuel and power starvation are both zero, while logistic-science output remains zero.

Therefore the terminal failure cannot be explained by final energy availability alone.

The replicated frontier remains:

1. local resource and manufacturing cells succeed;
2. the system reaches red science and electronic circuits;
3. producer outputs exist;
4. global processing reach remains incomplete;
5. the diagnostic layer recognizes the structural defect;
6. the runner lacks authority to execute the proposed processing placement;
7. green science is never materialized.

## 6. Aggregate position after four seeds

After 20261001–20261004:

- valid observations: 4/4;
- closed-loop successes: 0/4;
- Logistic science bottleneck: 4/4;
- green-science output: 0 in all four;
- mean autonomy score: 0.4375;
- mean physical processing coverage: 45.83%;
- median physical processing coverage: 50%;
- mean manual logistics calls: 54;
- mean endogenous rate: 0.60628371/s;
- mean intervention rate: 0.64728763/s.

These values are descriptive exploratory statistics. They are not confirmatory inference.

## 7. Decision

Seed 20261004 is retained as a valid exploratory observation.

Seed 20261005 becomes eligible only after this checkpoint is documented, tested, committed and the phase state is regenerated.
