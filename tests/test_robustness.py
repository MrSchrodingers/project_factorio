from pathlib import Path

from factorio_ai_lab.learning.robustness import OpenPlayRobustnessGate


def test_robustness_gate_requires_distinct_seeds(tmp_path: Path) -> None:
    gate = OpenPlayRobustnessGate(tmp_path / "robustness.json", required_passes=3)
    config = {"layout": 1}
    first = gate.record(
        champion_run_id="champion",
        configuration=config,
        run_id="r1",
        seed=10,
        passed=True,
    )
    second = gate.record(
        champion_run_id="champion",
        configuration=config,
        run_id="r2",
        seed=10,
        passed=True,
    )
    third = gate.record(
        champion_run_id="champion",
        configuration=config,
        run_id="r3",
        seed=11,
        passed=True,
    )
    assert not first["qualified"]
    assert not second["qualified"]
    assert not third["qualified"]
    final = gate.record(
        champion_run_id="champion",
        configuration=config,
        run_id="r4",
        seed=12,
        passed=True,
    )
    assert final["qualified"]
    assert final["distinct_pass_seed_count"] == 3


def test_robustness_state_resets_on_configuration_change(tmp_path: Path) -> None:
    gate = OpenPlayRobustnessGate(tmp_path / "robustness.json")
    gate.record(
        champion_run_id="champion",
        configuration={"margin": 8},
        run_id="r1",
        seed=1,
        passed=True,
    )
    state = gate.record(
        champion_run_id="champion",
        configuration={"margin": 4},
        run_id="r2",
        seed=2,
        passed=True,
    )
    assert state["pass_count"] == 1
    assert state["attempts"][0]["run_id"] == "r2"


def test_pending_only_after_success(tmp_path: Path) -> None:
    gate = OpenPlayRobustnessGate(tmp_path / "robustness.json")
    config = {"margin": 4}
    gate.record(
        champion_run_id="champion",
        configuration=config,
        run_id="r1",
        seed=1,
        passed=True,
    )
    assert gate.pending_for(
        champion_run_id="champion",
        configuration=config,
    )
    gate.record(
        champion_run_id="champion",
        configuration=config,
        run_id="r2",
        seed=2,
        passed=False,
    )
    assert not gate.pending_for(
        champion_run_id="champion",
        configuration=config,
    )
