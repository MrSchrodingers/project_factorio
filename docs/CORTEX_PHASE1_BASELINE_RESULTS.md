# Corrected Baseline — Exploratory Summary

- seeds observed: 5
- valid seeds: 5
- invalid seeds: 0
- closed-loop successes: 0
- green-science successes: 0
- bottlenecks: {'Logistic science': 5}

## Seed results

| Seed | Valid | Completed stages | Bottleneck | Closed loop | Green science | Halt cause | Autonomy | Manual logistics | Physical coverage |
|---:|---|---:|---|---|---:|---|---:|---:|---:|
| 20261001 | yes | 14 | Logistic science | False | 0.0 | none_observed | 0.5 | 53.0 | 0.3333333333333333 |
| 20261002 | yes | 14 | Logistic science | False | 0.0 | fuel_starvation | 0.375 | 53.0 | 0.5 |
| 20261003 | yes | 14 | Logistic science | False | 0.0 | fuel_starvation | 0.375 | 55.0 | 0.5 |
| 20261004 | yes | 14 | Logistic science | False | 0.0 | none_observed | 0.5 | 55.0 | 0.5 |
| 20261005 | yes | 14 | Logistic science | False | 0.0 | none_observed | 0.5 | 55.0 | 0.5 |

## Aggregate metrics

| Metric | n | Mean | Median | Sample SD | Q1 | Q3 | IQR | Min | Max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| autonomy_score | 5 | 0.45 | 0.5 | 0.06846531968814576 | 0.375 | 0.5 | 0.125 | 0.375 | 0.5 |
| manual_logistics_calls | 5 | 54.2 | 55.0 | 1.0954451150103321 | 53.0 | 55.0 | 2.0 | 53.0 | 55.0 |
| physical_processing_coverage | 5 | 0.4666666666666667 | 0.5 | 0.07453559924999299 | 0.5 | 0.5 | 0.0 | 0.3333333333333333 | 0.5 |
| fuel_starved_entities | 5 | 1.2 | 0.0 | 2.16794833886788 | 0.0 | 1.0 | 1.0 | 0.0 | 5.0 |
| power_starved_entities | 5 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| external_dependencies | 5 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| endogenous_rate_per_s | 5 | 0.606853028 | 0.60628371 | 0.0012730337488142173 | 0.60628371 | 0.60628371 | 0.0 | 0.60628371 | 0.6091303 |
| intervention_rate_per_s | 5 | 0.647391492 | 0.64764386 | 0.0005609834647028478 | 0.647004 | 0.64780694 | 0.0008029400000000297 | 0.64660593 | 0.64789673 |
| productive_runtime_s | 5 | 668.2166666666667 | 680.2166666666667 | 26.832815729997478 | 680.2166666666667 | 680.2166666666667 | 0.0 | 620.2166666666667 | 680.2166666666667 |
| route_cost | 5 | 8.25 | 8.25 | 0.0 | 8.25 | 8.25 | 0.0 | 8.25 | 8.25 |
| route_turns | 5 | 1.0 | 1.0 | 0.0 | 1.0 | 1.0 | 0.0 | 1.0 | 1.0 |

## Interpretation rule

Five exploratory seeds are complete; this is a descriptive exploratory baseline, not a confirmatory performance claim.
Invalid seeds stay visible and must be explained; failed agent runs stay in the sample.
The frozen confirmatory seeds remain unspent for later paired Cortex comparisons.
