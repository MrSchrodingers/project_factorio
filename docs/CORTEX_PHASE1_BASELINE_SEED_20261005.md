# Cortex Baseline Exploratory — Seed 20261005

**Protocol:** cortex_baseline_protocol_v1
**Mode:** exploratory
**Seed:** 20261005
**Verdict:** VALID BASELINE OBSERVATION
**Release under test:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Run:** curriculum-20260924T143812Z

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

This is the fifth independent seed to reach the same terminal frontier.

## 3. Primary measurements

| Metric | Seed 20261005 |
|---|---:|
| autonomy_score | 0.5 |
| closed_loop_autonomy | false |
| physical_processing_coverage | 0.5 |
| manual_logistics_calls | 55 |
| fuel_starved_entities | 0 |
| power_starved_entities | 0 |
| external_dependencies | 0 |
| endogenous_rate_per_s | 0.6091303 |
| intervention_rate_per_s | 0.64780694 |
| total_rate_per_s | 1.25693724 |
| productive_runtime_s | 680.2167 |
| route_cost | 8.25 |
| route_turns | 1 |

## 4. Repair anatomy

Fuel starvation was repaired to zero. The structural repair was again proposed but not executable:

    symptom = producer_output_unprocessed:output_buffered_not_processed
    intent = place_processing_for_buffered_output
    executed = false
    not_executed = no_runner_binding_for_intent

Therefore the same architectural frontier appears in a seed with zero final fuel/power starvation.

## 5. Interpretation

The fifth seed closes the exploratory series with the same qualitative result:

- local cells and intermediate manufacturing succeed;
- green science remains absent;
- closed-loop autonomy remains false;
- structural diagnosis exists;
- structural authority is missing.

The repeated failure is not explained by final energy availability alone.

## 6. Decision

Seed 20261005 is retained as a valid exploratory observation.

The exploratory baseline series is complete at 5/5 valid seeds. The confirmatory seeds 20261101–20261110 remain frozen and unspent for later paired Cortex evaluation.
