# Corrected Baseline — Exploratory Summary

- seeds observed: 3
- valid seeds: 3
- invalid seeds: 0
- closed-loop successes: 0

## Seed results

| Seed | Valid | Completed stages | Closed loop | Halt cause | Autonomy | Manual logistics | Physical coverage |
|---:|---|---:|---|---|---:|---:|---:|
| 20261001 | yes | 14 | False | none_observed | 0.5 | 53.0 | 0.3333333333333333 |
| 20261002 | yes | 14 | False | fuel_starvation | 0.375 | 53.0 | 0.5 |
| 20261003 | yes | 14 | False | fuel_starvation | 0.375 | 55.0 | 0.5 |

## Aggregate metrics

| Metric | n | Mean | Median | Min | Max |
|---|---:|---:|---:|---:|---:|
| autonomy_score | 3 | 0.4166666666666667 | 0.375 | 0.375 | 0.5 |
| manual_logistics_calls | 3 | 53.666666666666664 | 53.0 | 53.0 | 55.0 |
| physical_processing_coverage | 3 | 0.4444444444444444 | 0.5 | 0.3333333333333333 | 0.5 |
| fuel_starved_entities | 3 | 2.0 | 1.0 | 0.0 | 5.0 |
| power_starved_entities | 3 | 0.0 | 0.0 | 0.0 | 0.0 |
| external_dependencies | 3 | 0.0 | 0.0 | 0.0 | 0.0 |
| endogenous_rate_per_s | 3 | 0.60628371 | 0.60628371 | 0.60628371 | 0.60628371 |
| intervention_rate_per_s | 3 | 0.6470845966666666 | 0.647004 | 0.64660593 | 0.64764386 |
| productive_runtime_s | 3 | 660.2166666666667 | 680.2166666666667 | 620.2166666666667 | 680.2166666666667 |
| route_cost | 3 | 8.25 | 8.25 | 8.25 | 8.25 |
| route_turns | 3 | 1.0 | 1.0 | 1.0 | 1.0 |

## Interpretation rule

With fewer than five exploratory seeds this document is a run ledger, not a statistical conclusion.
Invalid seeds stay visible and must be explained; failed agent runs stay in the sample.
