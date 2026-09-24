# Cortex Baseline Exploratory — Seed 20261003

**Protocol:** cortex_baseline_protocol_v1
**Mode:** exploratory
**Seed:** 20261003
**Verdict:** VALID BASELINE OBSERVATION
**Release under test:** 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac
**Run:** curriculum-20260924T055231Z

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

The same high-level bottleneck has now appeared independently in seeds 20261001, 20261002 and 20261003.

## 3. Primary measurements

| Metric | Seed 20261003 |
|---|---:|
| autonomy_score | 0.375 |
| closed_loop_autonomy | false |
| physical_processing_coverage | 0.5 |
| manual_logistics_calls | 55 |
| fuel_starved_entities | 5 |
| power_starved_entities | 0 |
| external_dependencies | 0 |
| endogenous_rate_per_s | 0.60628371 |
| intervention_rate_per_s | 0.647004 |
| total_rate_per_s | 1.25328771 |
| productive_runtime_s | 680.2167 |
| route_cost | 8.25 |
| route_turns | 1 |

## 4. Interpretation

This is the third independent seed to reach the same frontier and fail to materialize green science.

The replicated pattern is:

1. local extraction, smelting, steam power, red science and electronic-circuit stages can be completed;
2. endogenous production remains stable around 0.6063/s in the recorded aggregate;
3. manual logistics remains high;
4. closed-loop autonomy remains false;
5. Logistic science remains the terminal bottleneck;
6. green science output remains zero.

The seed differs in final starvation severity: five entities remained fuel-starved. This variance does not change the structural observation that topology/repair authority remains the repeated limiting frontier.

## 5. Causal boundary

No gameplay, planner, repair or learning code was changed between seeds 20261001–20261003. All three used the exact frozen scientific runtime 95c34a53cf1e6f2c4cc73b9c6d7ffd497775c1ac.

Dashboard, documentation, launcher and observability tooling evolved outside that runtime and therefore do not alter the policy under test.

## 6. Decision

Seed 20261003 is retained as a valid exploratory observation.

Seed 20261004 becomes eligible only after:

- aggregate analyzer rerun;
- protocol/checklists updated;
- storage preflight qualified;
- machine-readable phase state regenerated;
- checkpoint committed and pushed.
