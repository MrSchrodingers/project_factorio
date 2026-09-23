"""Fixed-map-set evaluation: same worlds for every genome, worst case kept."""

from __future__ import annotations

from typing import Any

import pytest

from factorio_ai_lab.learning.map_suite import (
    FixedMapSetEvaluator,
    MapSpec,
    MapSuite,
    MapSuiteError,
    WorldMismatchError,
    aggregate_fitness,
    patch_footprint,
    rank_genomes,
    world_fingerprint,
    world_signature,
    world_signature_command,
)


def _patch(name: str, cell_x: int, cell_y: int, count: int = 600) -> dict[str, Any]:
    return {
        "name": name,
        "cell_x": cell_x,
        "cell_y": cell_y,
        "count": count,
        "amount": count * 1000,
        "min_x": cell_x * 32 + 1.5,
        "max_x": cell_x * 32 + 24.5,
        "min_y": cell_y * 32 + 1.5,
        "max_y": cell_y * 32 + 24.5,
    }


def _payload(*, map_seed: int = 2859378883, patches: list | None = None) -> dict:
    return {
        "connected": True,
        "surface": "nauvis",
        "tick": 6907967,
        "map_seed": map_seed,
        "width": 1000,
        "height": 1000,
        "scan_radius": 192,
        "cell_size": 32,
        "patches": patches
        if patches is not None
        else [_patch("iron-ore", 0, 2), _patch("copper-ore", -2, 2)],
    }


def test_command_is_read_only_and_parameterised() -> None:
    command = world_signature_command(radius=96, cell_size=16)
    assert "local radius=96" in command
    assert "local cell=16" in command
    assert "find_entities_filtered" in command
    for writer in ("create_entity", "destroy", "set_tiles", "insert", "teleport"):
        assert writer not in command


def test_signature_survives_mining_and_ordering() -> None:
    reference = world_signature(_payload())
    mined = _payload(
        patches=[_patch("copper-ore", -2, 2, count=3), _patch("iron-ore", 0, 2, count=7)]
    )
    assert world_signature(mined) == reference


def test_signature_separates_worlds() -> None:
    reference = world_signature(_payload())
    other_seed = world_signature(_payload(map_seed=44340))
    other_terrain = world_signature(
        _payload(patches=[_patch("iron-ore", 3, 5), _patch("copper-ore", -2, 2)])
    )
    assert other_seed != reference
    assert other_terrain != reference


def test_signature_accepts_the_lua_object_form_of_an_empty_array() -> None:
    assert patch_footprint({"patches": {}}) == []
    assert world_signature(_payload(patches=[])) != world_signature(_payload())


def test_signature_refuses_a_disconnected_payload() -> None:
    with pytest.raises(MapSuiteError):
        world_signature({"connected": False, "error": "agent character unavailable"})


def test_fingerprint_reports_patch_extent() -> None:
    fingerprint = world_fingerprint(_payload())
    assert fingerprint["world_signature"] == world_signature(_payload())
    assert fingerprint["map_seed"] == 2859378883
    assert fingerprint["resources"]["iron-ore"]["cells"] == 1
    assert fingerprint["resources"]["iron-ore"]["min_x"] == 1.5


def _suite() -> MapSuite:
    return MapSuite(
        suite_id="open-play-3",
        maps=(
            MapSpec(map_id="m1", world_signature="world-a", seed=101),
            MapSpec(map_id="m2", world_signature="world-b", seed=102),
            MapSpec(map_id="m3", world_signature="world-c", seed=103),
        ),
    )


def test_suite_refuses_maps_that_are_the_same_world() -> None:
    with pytest.raises(MapSuiteError, match="sharing a world signature"):
        MapSuite(
            suite_id="collapsed",
            maps=(
                MapSpec(map_id="m1", world_signature="world-a", seed=1),
                MapSpec(map_id="m2", world_signature="world-a", seed=2),
            ),
        )


def test_suite_refuses_a_map_without_world_evidence() -> None:
    with pytest.raises(MapSuiteError, match="without a world signature"):
        MapSuite(
            suite_id="unverified",
            maps=(MapSpec(map_id="m1", world_signature="", seed=1),),
        )


def test_suite_signature_ignores_map_order() -> None:
    reversed_suite = MapSuite(
        suite_id="open-play-3",
        maps=tuple(reversed(_suite().maps)),
    )
    assert reversed_suite.suite_signature == _suite().suite_signature


def test_aggregate_reports_mean_and_worst_case() -> None:
    suite = _suite()
    result = aggregate_fitness(suite, {"m1": 10.0, "m2": 4.0, "m3": 1.0})
    assert result["complete"] is True
    assert result["mean"] == pytest.approx(5.0)
    assert result["worst"] == pytest.approx(1.0)
    assert result["best"] == pytest.approx(10.0)
    assert result["worst_map"] == "m3"
    assert result["missing_maps"] == []


def test_aggregate_flags_partial_coverage() -> None:
    result = aggregate_fitness(_suite(), {"m1": 10.0})
    assert result["complete"] is False
    assert result["missing_maps"] == ["m2", "m3"]


def test_aggregate_refuses_scores_from_outside_the_suite() -> None:
    with pytest.raises(MapSuiteError, match="outside suite"):
        aggregate_fitness(_suite(), {"m1": 1.0, "other": 2.0})


def test_evaluator_runs_every_map_and_verifies_the_world() -> None:
    suite = _suite()
    visited: list[str] = []

    def evaluate(spec: MapSpec) -> dict[str, Any]:
        visited.append(spec.map_id)
        return {"fitness": 3.0, "world_signature": spec.world_signature}

    result = FixedMapSetEvaluator(suite).evaluate("genome-1", evaluate)
    assert visited == ["m1", "m2", "m3"]
    assert result["world_evidence"] == "verified"
    assert result["complete"] is True
    assert result["genome_id"] == "genome-1"


def test_evaluator_rejects_a_run_on_the_wrong_world() -> None:
    suite = _suite()

    def evaluate(spec: MapSpec) -> dict[str, Any]:
        return {"fitness": 3.0, "world_signature": "world-a"}

    with pytest.raises(WorldMismatchError, match="m2"):
        FixedMapSetEvaluator(suite).evaluate("genome-1", evaluate)


def test_evaluator_marks_missing_world_evidence() -> None:
    result = FixedMapSetEvaluator(_suite()).evaluate("genome-1", lambda spec: 2.0)
    assert result["world_evidence"] == "absent"
    assert result["mean"] == pytest.approx(2.0)


def test_ranking_prefers_the_worst_case_over_the_mean() -> None:
    suite = _suite()
    specialist = aggregate_fitness(suite, {"m1": 12.0, "m2": 1.0, "m3": 0.0})
    specialist["genome_id"] = "memoriser"
    generalist = aggregate_fitness(suite, {"m1": 4.0, "m2": 4.0, "m3": 4.0})
    generalist["genome_id"] = "engineer"

    assert specialist["mean"] > generalist["mean"]
    ordered = rank_genomes([specialist, generalist])
    assert [row["genome_id"] for row in ordered] == ["engineer", "memoriser"]
    assert ordered[0]["rank"] == 1


def test_ranking_refuses_incomplete_coverage() -> None:
    suite = _suite()
    partial = aggregate_fitness(suite, {"m1": 9.0})
    partial["genome_id"] = "partial"
    full = aggregate_fitness(suite, {"m1": 1.0, "m2": 1.0, "m3": 1.0})
    full["genome_id"] = "full"
    with pytest.raises(MapSuiteError, match="every map"):
        rank_genomes([partial, full])


def test_ranking_refuses_results_from_different_suites() -> None:
    first = aggregate_fitness(_suite(), {"m1": 1.0, "m2": 1.0, "m3": 1.0})
    first["genome_id"] = "a"
    other_suite = MapSuite(
        suite_id="other",
        maps=(MapSpec(map_id="m1", world_signature="world-z", seed=9),),
    )
    second = aggregate_fitness(other_suite, {"m1": 1.0})
    second["genome_id"] = "b"
    with pytest.raises(MapSuiteError, match="different map suites"):
        rank_genomes([first, second])
