from __future__ import annotations

from copy import deepcopy

import pytest

from factorio_ai_lab.cortex.causal_protocol import (
    CONFIRMATORY_RESERVED,
    EVALUATION_SEEDS,
    MEMORY_ABLATED,
    MEMORY_ON,
    PILOT_SEEDS,
    TASK_FAMILIES,
    ProtocolValidationError,
    build_protocol_manifest,
    canonical_sha256,
    validate_protocol,
)


def _contains_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        if forbidden & set(value):
            return True
        return any(_contains_key(item, forbidden) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def test_f4c_manifest_is_deterministic_and_valid() -> None:
    first = build_protocol_manifest()
    second = build_protocol_manifest()

    assert first == second
    assert canonical_sha256(first) == canonical_sha256(second)
    summary = validate_protocol(first)
    assert summary["evaluation_pair_count"] == 20
    assert summary["pilot_pair_count"] == 8
    assert summary["task_family_count"] == 4


def test_f4c_seed_partitions_are_disjoint_and_confirmatory_frozen() -> None:
    manifest = build_protocol_manifest()
    partitions = manifest["seed_partitions"]

    pilot = set(partitions["pilot"])
    evaluation = set(partitions["evaluation"])
    confirmatory = set(partitions["confirmatory_reserved"])

    assert pilot == set(PILOT_SEEDS)
    assert evaluation == set(EVALUATION_SEEDS)
    assert confirmatory == set(CONFIRMATORY_RESERVED)
    assert not (pilot & evaluation)
    assert not (pilot & confirmatory)
    assert not (evaluation & confirmatory)


def test_f4c_task_surface_is_diverse_and_encodes_no_solution_label() -> None:
    manifest = build_protocol_manifest()
    tasks = manifest["task_design"]["evaluation_tasks"]

    assert {row["family"] for row in tasks} == set(TASK_FAMILIES)
    assert len({row["task_id"] for row in tasks}) == 20
    assert all(len(row["spec"]["candidate_classes"]) >= 3 for row in tasks)
    assert all(len(row["spec"]["hard_postconditions"]) == 4 for row in tasks)
    assert not _contains_key(
        tasks,
        {"expected_action", "optimal_action", "solution", "winner", "ground_truth_action"},
    )


def test_f4c_counterbalancing_statistics_and_missingness_are_frozen() -> None:
    manifest = build_protocol_manifest()
    schedule = manifest["counterbalancing"]["schedule"]

    assert sum(row["first_condition"] == MEMORY_ON for row in schedule) == 10
    assert sum(row["first_condition"] == MEMORY_ABLATED for row in schedule) == 10
    assert manifest["statistics"]["test"] == "exact_paired_sign_flip_randomization"
    assert (
        manifest["statistics"]["enumeration"]
        == "all_2^m_sign_assignments_for_m_valid_pairs"
    )
    assert manifest["statistics"]["minimum_practically_relevant_effect_delta_J"] == 0.05
    assert manifest["statistics"]["minimum_analyzable_pairs"] == 16
    assert manifest["statistics"]["minimum_analyzable_pairs_per_family"] == 3
    assert "one_sided_exact_p<=0.05" in manifest["statistics"]["decision_rule"]
    assert manifest["statistics"]["pilot_excluded_from_primary_inference"] is True
    assert manifest["invalidity_and_missingness"]["missing_is_never_zero"] is True
    assert "exclude_the_entire_pair" in (
        manifest["invalidity_and_missingness"]["technical_invalidity_rule"]
    )
    assert "missing_is_never_zero" in manifest["primary_endpoint"]["missing_policy"]


def test_f4c_manifest_tampering_fails_closed() -> None:
    manifest = deepcopy(build_protocol_manifest())
    manifest["seed_partitions"]["evaluation"][0] = 20261101

    with pytest.raises(ProtocolValidationError):
        validate_protocol(manifest)
