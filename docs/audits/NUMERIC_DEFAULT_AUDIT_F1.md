# F1 Numeric Default Audit

- scanned_root: /srv/factorio-ai-lab/src
- findings_total: 487
- high_risk_candidates: 141

This is a static audit of places where absent values can be replaced by a numeric fallback.
A finding is not automatically a bug. Each high-risk candidate must be reviewed against
the measurement contract; the purpose is to make silent missing-to-number substitutions
enumerable and reviewable.

## High-risk candidates

| Path | Line | Kind | Key | Fallback |
|---|---:|---|---|---:|
| factorio_ai_lab/dashboard/rendering.py | 232 | mapping_get_numeric_default | water_tile_count | 0 |
| factorio_ai_lab/dashboard/rendering.py | 232 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/dashboard/rendering.py | 736 | mapping_get_numeric_default | amount | 1.0 |
| factorio_ai_lab/dashboard/rendering.py | 1044 | mapping_get_numeric_default | amount | 1.0 |
| factorio_ai_lab/dashboard/state.py | 2725 | mapping_get_numeric_default | entity_count | 0 |
| factorio_ai_lab/dashboard/state.py | 2829 | mapping_get_numeric_default | autonomy_soak_runtime_s | 0.0 |
| factorio_ai_lab/dashboard/state.py | 2829 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 2845 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/dashboard/state.py | 2847 | mapping_get_numeric_default | assisted_navigation_count | 0 |
| factorio_ai_lab/dashboard/state.py | 2861 | mapping_get_numeric_default | produced_rate | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3318 | mapping_get_numeric_default | iron-plate | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3318 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3319 | mapping_get_numeric_default | belt_smelting_plate_output | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3319 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3320 | mapping_get_numeric_default | iron_plate_output | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3320 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3324 | mapping_get_numeric_default | coal | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3324 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3325 | mapping_get_numeric_default | coal_output | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3325 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3329 | mapping_get_numeric_default | copper-ore | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3329 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3330 | mapping_get_numeric_default | copper_ore_output | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3330 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3334 | mapping_get_numeric_default | copper-plate | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3334 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3335 | mapping_get_numeric_default | copper_plate_output | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3335 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/dashboard/state.py | 3345 | mapping_get_numeric_default |  | 0 |
| factorio_ai_lab/dashboard/state.py | 3698 | mapping_get_numeric_default | water_tile_count | 0 |
| factorio_ai_lab/dashboard/state.py | 3700 | mapping_get_numeric_default | count | 0 |
| factorio_ai_lab/domain/state.py | 46 | mapping_get_numeric_default |  | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 330 | mapping_get_numeric_default | output | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 1226 | mapping_get_numeric_default |  | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 2830 | mapping_get_numeric_default | reward | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 2834 | mapping_get_numeric_default | reward | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 4766 | mapping_get_numeric_default | output | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5254 | mapping_get_numeric_default | output | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5304 | mapping_get_numeric_default | belt_smelting_plate_rate_per_s | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5304 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5308 | mapping_get_numeric_default | direct_smelting_plate_rate_per_s | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5308 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5386 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5402 | mapping_get_numeric_default | belt_count | 0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5422 | mapping_get_numeric_default | plate_rate_per_s | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5423 | mapping_get_numeric_default | rate_retention | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 5834 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 6166 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 6370 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 6390 | mapping_get_numeric_default | copper_smelting_fuel | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 6408 | mapping_get_numeric_default | copper_smelting_fuel | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 6417 | mapping_get_numeric_default | copper_smelting_fuel | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 6608 | mapping_get_numeric_default | refueled_count | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 7953 | mapping_get_numeric_default | count | 0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 8094 | mapping_get_numeric_default |  | 0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 8512 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 8679 | mapping_get_numeric_default | output | 0.0 |
| factorio_ai_lab/experiments/curriculum_runner.py | 8831 | mapping_get_numeric_default | output | 0.0 |
| factorio_ai_lab/experiments/evolution_loop.py | 541 | mapping_get_numeric_default | autonomy_route_detour_margin | 8 |
| factorio_ai_lab/experiments/evolution_loop.py | 541 | or_numeric_fallback |  | 8 |
| factorio_ai_lab/experiments/evolution_loop.py | 544 | mapping_get_numeric_default | autonomy_belt_margin | 6 |
| factorio_ai_lab/experiments/evolution_loop.py | 544 | or_numeric_fallback |  | 6 |
| factorio_ai_lab/experiments/evolution_loop.py | 629 | mapping_get_numeric_default | autonomy_belt_margin | 6 |
| factorio_ai_lab/experiments/evolution_loop.py | 629 | or_numeric_fallback |  | 6 |
| factorio_ai_lab/experiments/evolution_loop.py | 638 | mapping_get_numeric_default | autonomy_pole_margin | 10 |
| factorio_ai_lab/experiments/evolution_loop.py | 638 | or_numeric_fallback |  | 10 |
| factorio_ai_lab/experiments/evolution_loop.py | 705 | mapping_get_numeric_default | autonomy_layout_variant | 0 |
| factorio_ai_lab/experiments/evolution_loop.py | 705 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/evolution_loop.py | 709 | mapping_get_numeric_default | autonomy_commissioning_coal | 14 |
| factorio_ai_lab/experiments/evolution_loop.py | 709 | or_numeric_fallback |  | 14 |
| factorio_ai_lab/experiments/evolution_loop.py | 713 | mapping_get_numeric_default | autonomy_belt_margin | 6 |
| factorio_ai_lab/experiments/evolution_loop.py | 713 | or_numeric_fallback |  | 6 |
| factorio_ai_lab/experiments/evolution_loop.py | 717 | mapping_get_numeric_default | autonomy_pole_margin | 10 |
| factorio_ai_lab/experiments/evolution_loop.py | 717 | or_numeric_fallback |  | 10 |
| factorio_ai_lab/experiments/open_play_runner.py | 169 | mapping_get_numeric_default |  | 0.0 |
| factorio_ai_lab/experiments/open_play_runner.py | 169 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/experiments/open_play_runner.py | 955 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 969 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1460 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1487 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1490 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1517 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1716 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1719 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1722 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1741 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 1930 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 2231 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 2268 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 3135 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 3215 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 3325 | mapping_get_numeric_default | autonomy_layout_variant | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 3325 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 3332 | mapping_get_numeric_default | autonomy_commissioning_coal | 14 |
| factorio_ai_lab/experiments/open_play_runner.py | 3332 | or_numeric_fallback |  | 14 |
| factorio_ai_lab/experiments/open_play_runner.py | 3344 | mapping_get_numeric_default | autonomy_belt_margin | 6 |
| factorio_ai_lab/experiments/open_play_runner.py | 3344 | or_numeric_fallback |  | 6 |
| factorio_ai_lab/experiments/open_play_runner.py | 3351 | mapping_get_numeric_default | autonomy_pole_margin | 10 |
| factorio_ai_lab/experiments/open_play_runner.py | 3351 | or_numeric_fallback |  | 10 |
| factorio_ai_lab/experiments/open_play_runner.py | 3359 | mapping_get_numeric_default | autonomy_route_detour_margin | 8 |
| factorio_ai_lab/experiments/open_play_runner.py | 3359 | or_numeric_fallback |  | 8 |
| factorio_ai_lab/experiments/open_play_runner.py | 3605 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 3696 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4341 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4344 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4659 | mapping_get_numeric_default | belt_build_count | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4659 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4662 | mapping_get_numeric_default | pole_build_count | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4662 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4789 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4791 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4816 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4822 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 4992 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5116 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5119 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5304 | mapping_get_numeric_default | assisted_navigation_count | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5304 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5326 | mapping_get_numeric_default | assisted_navigation_count | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5326 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5441 | mapping_get_numeric_default | assisted_navigation_count | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5441 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5601 | mapping_get_numeric_default | autonomy_soak_runtime_s | 0.0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5601 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5665 | mapping_get_numeric_default | distinct_pass_seed_count | 0 |
| factorio_ai_lab/experiments/open_play_runner.py | 5665 | or_numeric_fallback |  | 0 |
| factorio_ai_lab/experiments/run_iron_miner.py | 218 | mapping_get_numeric_default | iron-ore | 0 |
| factorio_ai_lab/learning/archive.py | 408 | mapping_get_numeric_default |  | 0 |
| factorio_ai_lab/learning/lifelong.py | 173 | mapping_get_numeric_default |  | 0 |
| factorio_ai_lab/learning/lifelong.py | 264 | mapping_get_numeric_default |  | 0 |
| factorio_ai_lab/learning/map_suite.py | 174 | mapping_get_numeric_default | count | 0 |
| factorio_ai_lab/learning/telemetry.py | 145 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/learning/telemetry.py | 149 | mapping_get_numeric_default | endogenous_stockpile | 0.0 |
| factorio_ai_lab/learning/telemetry.py | 155 | or_numeric_fallback |  | 0.0 |
| factorio_ai_lab/learning/telemetry.py | 157 | mapping_get_numeric_default | safety_stock_target | 0.0 |
| factorio_ai_lab/planning/dependency_plan.py | 844 | mapping_get_numeric_default |  | 1 |
| factorio_ai_lab/planning/production.py | 17 | mapping_get_numeric_default |  | 0.0 |
| factorio_ai_lab/planning/progression.py | 25 | mapping_get_numeric_default |  | 0.0 |
| factorio_ai_lab/planning/progression.py | 29 | mapping_get_numeric_default |  | 0 |
| factorio_ai_lab/planning/runtime_catalog.py | 305 | mapping_get_numeric_default | amount | 0.0 |
| factorio_ai_lab/planning/runtime_catalog.py | 311 | mapping_get_numeric_default | amount | 0.0 |

## Interpretation

High-risk means the expression mentions a metric-like token. It does not mean the fallback
is wrong: counters that are defined as zero when absent are legitimate. The review must
distinguish those from unread/failed measurements, which must use EvidenceStatus.missing
or EvidenceStatus.invalid instead of a number.

## Machine-readable output

The JSON output generated with --json is the exhaustive list and should be attached to
baseline evidence under runs/audits/.
