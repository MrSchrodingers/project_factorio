from __future__ import annotations

import hashlib
import json
import random
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = "cortex_f4c_causal_ablation_protocol_v1"
PROTOCOL_ID = "cortex-f4c-memory-ablation-transfer-v1"
CONFIRMATORY_RESERVED = tuple(range(20261101, 20261111))
PILOT_SEEDS = tuple(range(20261201, 20261209))
EVALUATION_SEEDS = tuple(range(20261221, 20261241))
TASK_FAMILIES = (
    "structural_flow_repair",
    "fuel_energy_recovery",
    "spatial_logistics_routing",
    "production_transition_planning",
)
MEMORY_ON = "memory_on"
MEMORY_ABLATED = "memory_ablated"


class ProtocolValidationError(ValueError):
    pass


def _rng(family: str, seed: int) -> random.Random:
    return random.Random(f"{PROTOCOL_ID}:{family}:{seed}")


def _structural_task(seed: int) -> dict[str, Any]:
    rng = _rng("structural_flow_repair", seed)
    return {
        "topology": rng.choice(["branched", "convergent", "offset_sink", "partial_bus"]),
        "producer_count": rng.randint(2, 5),
        "sink_distance_tiles": rng.randint(7, 19),
        "buffered_output_units": rng.randint(12, 96),
        "blocked_links": rng.randint(1, 3),
        "inventory_pressure": rng.choice(["low", "medium", "high"]),
        "candidate_classes": [
            "placement_processing",
            "reroute_logistics",
            "rebuild_segment",
        ],
        "hard_postconditions": [
            "processor_exists",
            "producer_reaches_processor",
            "processor_output_increases",
            "no_new_dead_end",
        ],
    }


def _fuel_task(seed: int) -> dict[str, Any]:
    rng = _rng("fuel_energy_recovery", seed)
    return {
        "terminal_condition": rng.choice(["no_fuel", "power_deficit", "mixed_energy_deficit"]),
        "burner_entities": rng.randint(2, 7),
        "local_fuel_units": rng.randint(0, 4),
        "coal_distance_tiles": rng.randint(8, 28),
        "power_demand_kw": rng.randint(180, 900),
        "bootstrap_inventory_pressure": rng.choice(["none", "low", "constrained"]),
        "candidate_classes": [
            "local_refuel",
            "route_endogenous_fuel",
            "establish_power_generation",
        ],
        "hard_postconditions": [
            "energy_dependency_resolved",
            "target_chain_resumes",
            "bootstrap_dependency_not_increased",
            "no_validated_capability_regresses",
        ],
    }


def _spatial_task(seed: int) -> dict[str, Any]:
    rng = _rng("spatial_logistics_routing", seed)
    width = rng.randint(28, 44)
    height = rng.randint(24, 40)
    start = [rng.randint(2, 6), rng.randint(2, 6)]
    goal = [rng.randint(width - 7, width - 3), rng.randint(height - 7, height - 3)]
    obstacles: list[list[int]] = []
    for _ in range(rng.randint(3, 6)):
        x = rng.randint(8, max(8, width - 12))
        y = rng.randint(6, max(6, height - 10))
        w = rng.randint(2, 5)
        h = rng.randint(2, 5)
        obstacles.append([x, y, w, h])
    return {
        "grid": [width, height],
        "start": start,
        "goal": goal,
        "obstacle_rectangles": obstacles,
        "turn_penalty": round(rng.uniform(0.10, 1.20), 3),
        "underground_budget": rng.randint(0, 4),
        "candidate_classes": [
            "weighted_astar",
            "detour_with_underground",
            "alternate_corridor",
        ],
        "hard_postconditions": [
            "route_found",
            "route_collision_free",
            "endpoints_connected",
            "route_within_resource_budget",
        ],
    }


def _production_task(seed: int) -> dict[str, Any]:
    rng = _rng("production_transition_planning", seed)
    stage = rng.choice(["electric_mining", "automation_science", "logistic_science"])
    return {
        "target_stage": stage,
        "iron_available": rng.randint(20, 180),
        "copper_available": rng.randint(8, 120),
        "wood_available": rng.randint(4, 64),
        "coal_available": rng.randint(0, 80),
        "required_buffer_margin": rng.randint(2, 12),
        "layout_pressure": rng.choice(["compact", "moderate", "expanded"]),
        "candidate_classes": [
            "buffer_first",
            "power_first",
            "route_first",
            "craft_chain_first",
        ],
        "hard_postconditions": [
            "required_material_budget_satisfied",
            "dependency_order_valid",
            "target_stage_functional",
            "no_validated_capability_regresses",
        ],
    }


def _task_payload(family: str, seed: int) -> dict[str, Any]:
    if family == "structural_flow_repair":
        return _structural_task(seed)
    if family == "fuel_energy_recovery":
        return _fuel_task(seed)
    if family == "spatial_logistics_routing":
        return _spatial_task(seed)
    if family == "production_transition_planning":
        return _production_task(seed)
    raise ProtocolValidationError(f"unknown task family: {family}")


def _partition_tasks(
    seeds: tuple[int, ...],
    per_family: int,
    partition: str,
) -> list[dict[str, Any]]:
    expected = len(TASK_FAMILIES) * per_family
    if len(seeds) != expected:
        raise ProtocolValidationError(
            f"{partition} seed count must be {expected}, got {len(seeds)}"
        )
    tasks: list[dict[str, Any]] = []
    cursor = 0
    for family in TASK_FAMILIES:
        for seed in seeds[cursor : cursor + per_family]:
            tasks.append(
                {
                    "task_id": f"{partition}:{family}:{seed}",
                    "partition": partition,
                    "family": family,
                    "seed": seed,
                    "generator_version": "cortex_f4c_taskgen_v1",
                    "spec": _task_payload(family, seed),
                }
            )
        cursor += per_family
    return tasks


def canonical_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def task_fingerprint(task: dict[str, Any]) -> str:
    return canonical_sha256(task)


def build_protocol_manifest() -> dict[str, Any]:
    pilot_tasks = _partition_tasks(PILOT_SEEDS, 2, "pilot")
    evaluation_tasks = _partition_tasks(EVALUATION_SEEDS, 5, "evaluation")
    schedule = []
    for index, task in enumerate(evaluation_tasks):
        first = MEMORY_ON if index % 2 == 0 else MEMORY_ABLATED
        second = MEMORY_ABLATED if first == MEMORY_ON else MEMORY_ON
        schedule.append(
            {
                "task_id": task["task_id"],
                "seed": task["seed"],
                "family": task["family"],
                "first_condition": first,
                "second_condition": second,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "status": "frozen",
        "authority": "shadow",
        "continuous_authority": False,
        "scientific_question": (
            "Does access to the frozen Cortex cognitive-memory substrate improve "
            "performance on held-out transfer tasks under matched non-memory capabilities?"
        ),
        "claim_boundary": {
            "primary_claim": (
                "memory access improves paired held-out task utility relative to "
                "retrieval ablation"
            ),
            "does_not_claim": [
                "continuous autonomous authority",
                "optimal retrieval weights",
                "general intelligence",
                "confirmatory-seed performance",
                "benefit from online learning during evaluation",
            ],
        },
        "source_memory": {
            "path": "runs/ledger/cortex_cognitive_memory.sqlite3",
            "schema_version": "cortex_cognitive_memory_v1",
            "item_count": 222,
            "occurrence_count": 759,
            "manifest_sha256": (
                "e6aa69816fe992f6b2a6afc8aff529fa"
                "5f1572106ca0939cee830af5a6cf3399"
            ),
            "f4a_artifact_sha256": (
                "f45e31785c17cd6222a57937564036db"
                "dd4976ee1d6376b61f340a9d70066228"
            ),
            "f4b_artifact_sha256": (
                "5fc37b0cee5f121c5ff6b6054fc45f4"
                "b0a09bad851e34793958bdd3dc1c5a801"
            ),
            "freeze_scope": "entire_f4c_experiment",
            "evaluation_write_policy": "quarantine_until_all_evaluation_pairs_complete",
        },
        "seed_partitions": {
            "pilot": list(PILOT_SEEDS),
            "evaluation": list(EVALUATION_SEEDS),
            "confirmatory_reserved": list(CONFIRMATORY_RESERVED),
            "disjoint_required": True,
        },
        "task_design": {
            "generator_version": "cortex_f4c_taskgen_v1",
            "pilot_tasks": pilot_tasks,
            "evaluation_tasks": evaluation_tasks,
            "minimum_task_families": 4,
            "minimum_evaluation_pairs_per_family": 5,
            "solution_labels_forbidden": True,
            "task_generation_frozen_before_outcomes": True,
        },
        "pairing": {
            "conditions": [MEMORY_ON, MEMORY_ABLATED],
            "same_world_checkpoint": True,
            "restore_checkpoint_between_arms": True,
            "same_code_revision": True,
            "same_candidate_generation": True,
            "same_deterministic_tools": True,
            "same_feasibility_rules": True,
            "same_action_surface": True,
            "same_runtime_budgets": True,
            "same_outcome_extractor": True,
            "online_updates_between_arms": False,
            "evaluation_memory_updates_before_completion": False,
            "memory_on": {
                "retrieval_mode": "frozen_f4b_snapshot",
                "writes": "quarantined",
            },
            "memory_ablated": {
                "retrieval_mode": "empty_retrieval_result",
                "writes": "quarantined",
                "unrelated_capabilities_removed": False,
            },
        },
        "counterbalancing": {
            "method": "explicit_balanced_schedule",
            "schedule": schedule,
            "memory_on_first": 10,
            "memory_ablated_first": 10,
        },
        "budgets": {
            "max_decisions": 12,
            "max_actions": 32,
            "max_game_ticks": 18000,
            "max_wall_clock_seconds": 300,
            "max_llm_calls": 2,
            "max_retrieval_queries": 4,
        },
        "primary_endpoint": {
            "name": "J",
            "direction": "higher_is_better",
            "range": [0.0, 1.0],
            "formula": (
                "0.55*functional_success + 0.25*goal_progress + "
                "0.10*(1-normalized_resource_cost) + "
                "0.10*(1-invalid_action_rate)"
            ),
            "functional_success": (
                "1 iff every predeclared hard postcondition is satisfied; otherwise 0"
            ),
            "goal_progress": (
                "satisfied_hard_postconditions / total_hard_postconditions"
            ),
            "normalized_resource_cost": (
                "0.5*min(1,action_count/max_actions) + "
                "0.5*min(1,observed_game_ticks/max_game_ticks)"
            ),
            "invalid_action_rate": (
                "invalid_or_refused_actions/proposed_actions; "
                "if proposed_actions=0 on an initially unsatisfied task, use 1"
            ),
            "missing_policy": (
                "missing_or_unmeasured_primary_component_invalidates_the_pair; "
                "missing_is_never_zero"
            ),
        },
        "statistics": {
            "analysis_unit": "paired_evaluation_task",
            "evaluation_pairs": 20,
            "primary_contrast": "delta_J = J_memory_on - J_memory_ablated",
            "primary_statistic": "mean_delta_J",
            "test": "exact_paired_sign_flip_randomization",
            "alternative": "greater",
            "alpha": 0.05,
            "test_assumption": (
                "under the sharp no-memory-effect null, paired condition labels are "
                "exchangeable; no asymptotic normal approximation is used"
            ),
            "enumeration": "all_2^m_sign_assignments_for_m_valid_pairs",
            "minimum_practically_relevant_effect_delta_J": 0.05,
            "decision_rule": (
                "support_F4_causal_memory_benefit_only_if one_sided_exact_p<=0.05 "
                "and two_sided_95pct_CI_lower_bound>0 and mean_delta_J>=0.05"
            ),
            "sample_size_rationale": (
                "fixed_n_20_before_outcomes: five independent held-out pairs per "
                "each of four task families. No trustworthy prior transfer-effect "
                "distribution exists, so no model-based power claim is made. "
                "The design prioritizes exact finite-sample inference, family "
                "coverage, and an immutable stopping rule; any larger design "
                "requires a new protocol version before evaluation begins"
            ),
            "minimum_analyzable_pairs": 16,
            "minimum_analyzable_pairs_per_family": 3,
            "insufficient_valid_pairs_result": (
                "inconclusive_protocol_execution_not_negative_memory_effect"
            ),
            "effect_sizes": [
                "mean_delta_J",
                "median_delta_J",
                "paired_cohens_dz",
            ],
            "confidence_interval": {
                "level": 0.95,
                "parameter": "constant_additive_shift_tau",
                "method": (
                    "invert_two_sided_exact_paired_sign_flip_randomization_test"
                ),
            },
            "secondary_endpoints": [
                "functional_success",
                "goal_progress",
                "normalized_resource_cost",
                "invalid_action_rate",
            ],
            "secondary_multiplicity": "holm_familywise_alpha_0.05_if_inferential_claimed",
            "family_stratification": "report_each_family_descriptively_and_with_effect_size",
            "pilot_excluded_from_primary_inference": True,
        },
        "invalidity_and_missingness": {
            "transport_or_tool_timeout": (
                "not_an_experimental_failure_until_process_lease_artifact_and_world_state_are_audited"
            ),
            "task_timeout_with_valid_instrumentation": "observed_task_failure",
            "predeclared_technical_invalidity": [
                "checkpoint_restore_mismatch",
                "code_revision_mismatch_between_arms",
                "source_memory_manifest_mismatch",
                "cross_arm_world_state_leakage",
                "evaluation_memory_contamination",
                "outcome_extractor_failure",
            ],
            "technical_invalidity_rule": (
                "exclude_the_entire_pair_from_primary_inference; do_not_replace "
                "with_a_new_seed_after_outcomes_are_visible"
            ),
            "interrupted_arm_recovery_rule": (
                "audit_process_lease_grant_artifact_and_world_state_first; resume "
                "the_same_arm_only_when_state_equivalence_is_proven, otherwise "
                "mark_the_pair_technically_invalid"
            ),
            "outcome_dependent_exclusion_forbidden": True,
            "missing_is_never_zero": True,
        },
        "stopping_rule": {
            "pilot_pairs": 8,
            "evaluation_pairs": 20,
            "optional_sample_size_change_after_evaluation_begins": False,
            "pilot_may_change_future_protocol_version_only": True,
            "pilot_outcomes_reused_as_evaluation": False,
        },
        "execution_gate": {
            "evolution_must_be_inactive": True,
            "continuous_authority_must_be_false": True,
            "confirmatory_seeds_must_remain_pending": True,
            "protocol_manifest_must_match_frozen_hash": True,
            "evaluation_harness_must_pass_before_seed_launch": True,
        },
    }


def validate_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    expected = build_protocol_manifest()
    if payload != expected:
        expected_hash = canonical_sha256(expected)
        actual_hash = canonical_sha256(payload)
        raise ProtocolValidationError(
            "protocol manifest differs from the frozen v1 specification: "
            f"expected={expected_hash} actual={actual_hash}"
        )

    partitions = payload["seed_partitions"]
    pilot = set(partitions["pilot"])
    evaluation = set(partitions["evaluation"])
    confirmatory = set(partitions["confirmatory_reserved"])
    if pilot & evaluation or pilot & confirmatory or evaluation & confirmatory:
        raise ProtocolValidationError("seed partitions are not disjoint")
    if confirmatory != set(CONFIRMATORY_RESERVED):
        raise ProtocolValidationError("confirmatory reservation changed")

    design = payload["task_design"]
    evaluation_tasks = design["evaluation_tasks"]
    pilot_tasks = design["pilot_tasks"]
    families = {row["family"] for row in evaluation_tasks}
    if families != set(TASK_FAMILIES):
        raise ProtocolValidationError("evaluation task families changed")
    if len(evaluation_tasks) != 20 or len(pilot_tasks) != 8:
        raise ProtocolValidationError("unexpected task count")

    fingerprints = [task_fingerprint(row) for row in evaluation_tasks]
    if len(set(fingerprints)) != len(fingerprints):
        raise ProtocolValidationError("evaluation task specs are not unique")

    schedule = payload["counterbalancing"]["schedule"]
    on_first = sum(row["first_condition"] == MEMORY_ON for row in schedule)
    ablated_first = sum(
        row["first_condition"] == MEMORY_ABLATED for row in schedule
    )
    if (on_first, ablated_first) != (10, 10):
        raise ProtocolValidationError("arm order is not balanced")

    missing = payload["primary_endpoint"]["missing_policy"]
    if "missing_is_never_zero" not in missing:
        raise ProtocolValidationError("missing-value policy is not fail-closed")

    return {
        "schema_version": payload["schema_version"],
        "protocol_id": payload["protocol_id"],
        "manifest_sha256": canonical_sha256(payload),
        "pilot_pair_count": len(pilot_tasks),
        "evaluation_pair_count": len(evaluation_tasks),
        "task_family_count": len(families),
        "evaluation_task_fingerprints": fingerprints,
        "memory_on_first": on_first,
        "memory_ablated_first": ablated_first,
        "confirmatory_reserved": sorted(confirmatory),
    }


def clone_manifest() -> dict[str, Any]:
    return deepcopy(build_protocol_manifest())
