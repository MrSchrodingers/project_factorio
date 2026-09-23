"""The robustness gate must qualify on distinct worlds, not on distinct seeds.

Measured on this cluster: eight consecutive open-play runs with seeds
20260931..20260938 ran on one terrain (iron at (27, 83), copper at
(-58.5, 83), coal at (27, 8.5) in every one of them), because the seed never
reaches the map generator. A gate that counts seeds calls that three
independent trials.
"""

from __future__ import annotations

from pathlib import Path

from factorio_ai_lab.learning.robustness import OpenPlayRobustnessGate

CONFIGURATION = {"layout": 1}


def _gate(tmp_path: Path, required_passes: int = 3) -> OpenPlayRobustnessGate:
    return OpenPlayRobustnessGate(
        tmp_path / "robustness.json",
        required_passes=required_passes,
    )


def _record(
    gate: OpenPlayRobustnessGate,
    run_id: str,
    seed: int,
    *,
    world: str | None = None,
    passed: bool = True,
) -> dict:
    return gate.record(
        champion_run_id="champion",
        configuration=CONFIGURATION,
        run_id=run_id,
        seed=seed,
        passed=passed,
        world_signature=world,
    )


def test_passes_without_terrain_evidence_never_qualify(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    state = {}
    for index, seed in enumerate((20260931, 20260932, 20260933), start=1):
        state = _record(gate, f"run-{index}", seed)

    assert state["pass_count"] == 3
    assert state["distinct_pass_seeds"] == [20260931, 20260932, 20260933]
    assert state["pass_seed_count"] == 3
    assert state["qualified"] is False
    # The progress numerator counts proven worlds, and none were proven.
    assert state["distinct_pass_seed_count"] == 0
    assert state["distinct_pass_world_count"] == 0
    assert state["unverified_pass_count"] == 3
    assert state["world_status"] == "unverified"
    assert "world_signature_missing" in state["blocking_reason"]


def test_one_world_under_many_seeds_is_reported_as_collapse(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    state = {}
    for index, seed in enumerate((20260931, 20260932, 20260933), start=1):
        state = _record(gate, f"run-{index}", seed, world="2e946aaac861f5e85460")

    assert state["qualified"] is False
    assert state["world_status"] == "collapsed"
    assert state["distinct_pass_world_count"] == 1
    assert state["distinct_pass_seed_count"] == 1
    assert state["seed_world_collisions"] == [
        {
            "world_signature": "2e946aaac861f5e85460",
            "seeds": [20260931, 20260932, 20260933],
            "run_ids": ["run-1", "run-2", "run-3"],
        }
    ]
    assert "seed_world_collapse" in state["blocking_reason"]


def test_distinct_worlds_qualify(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    state = {}
    for index, (seed, world) in enumerate(
        ((11, "world-a"), (12, "world-b"), (13, "world-c")), start=1
    ):
        state = _record(gate, f"run-{index}", seed, world=world)

    assert state["qualified"] is True
    assert state["world_status"] == "verified"
    assert state["blocking_reason"] is None
    assert state["distinct_pass_worlds"] == ["world-a", "world-b", "world-c"]
    assert state["distinct_pass_seed_count"] == 3
    assert state["qualifying_unit"] == "distinct_world_signature"


def test_two_worlds_are_not_three(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    state = {}
    for index, (seed, world) in enumerate(
        ((11, "world-a"), (12, "world-b"), (13, "world-b")), start=1
    ):
        state = _record(gate, f"run-{index}", seed, world=world)

    assert state["qualified"] is False
    assert state["distinct_pass_world_count"] == 2
    assert state["world_status"] == "collapsed"


def test_blocked_gate_stops_asking_for_the_same_run(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    _record(gate, "run-1", 20260931, world="same-world")
    assert gate.pending_for(
        champion_run_id="champion",
        configuration=CONFIGURATION,
    )
    _record(gate, "run-2", 20260932, world="same-world")
    assert not gate.pending_for(
        champion_run_id="champion",
        configuration=CONFIGURATION,
    )


def test_gate_still_collects_while_evidence_is_open(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    state = _record(gate, "run-1", 20260931)
    assert state["blocking_reason"] is None
    assert gate.pending_for(
        champion_run_id="champion",
        configuration=CONFIGURATION,
    )


def test_legacy_call_path_without_terrain_evidence_never_qualifies(
    tmp_path: Path,
) -> None:
    """The call site in `open_play_runner` passes no signature; it must not qualify.

    This is the exact call the live runner makes today. Before the gate
    counted worlds, three seeds on one terrain were enough to declare the
    champion qualified.
    """
    gate = _gate(tmp_path)
    state = {}
    for index, seed in enumerate((20260931, 20260932, 20260933), start=1):
        state = gate.record(
            champion_run_id="champion",
            configuration=CONFIGURATION,
            run_id=f"run-{index}",
            seed=seed,
            passed=True,
        )
    assert state["qualified"] is False
    assert state["distinct_pass_seed_count"] == 0
