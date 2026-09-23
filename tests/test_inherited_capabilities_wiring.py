"""The inheritance a generation starts on has to reach its fitness vector.

``FitnessVector.inherited_capabilities`` existed, ``compare_challenger``
respected it and the niche archive subtracted it, but nothing ever assigned
it: every capability that arrived with an ancestor's factory was credited to
the heir's genome. Generation 37 turned that from inert into load-bearing --
the world now starts with the ancestral factory in it -- so the wiring is
covered here, end to end.

Three states are covered, not two. ``None`` means no inheritance was in play.
An empty set means a factory was inherited and it carried no capability. The
third is an inheritance that was applied and whose capabilities could not be
read back: collapsing it into ``None`` would credit the genome with
everything it merely received, which is the failure this field exists to
prevent.
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Any

import pytest
from test_archive_integration import sandboxed_runs

from factorio_ai_lab.experiments import curriculum_runner, evolution_loop
from factorio_ai_lab.learning.survival import (
    RATE_PROTOCOL_OBSERVED_WINDOW,
    FitnessVector,
    InheritedCapabilities,
    compare_challenger,
    fitness_from_research,
)

BASE = frozenset({"iron_backbone"})

SOURCE_ROOT = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "factorio_ai_lab"
)
RUNNER_SOURCE = (SOURCE_ROOT / "experiments" / "curriculum_runner.py").read_text(
    encoding="utf-8"
)
LOOP_SOURCE = (SOURCE_ROOT / "experiments" / "evolution_loop.py").read_text(
    encoding="utf-8"
)


def _incumbent(capabilities: frozenset[str] = BASE) -> FitnessVector:
    """An incumbent measured by the same instrument as the challenger."""
    return FitnessVector(
        capabilities=capabilities,
        measurement_protocol=RATE_PROTOCOL_OBSERVED_WINDOW,
    )


def _challenger(
    achieved: set[str],
    inherited: InheritedCapabilities | None,
) -> FitnessVector:
    return fitness_from_research(
        metrics={},
        achieved=achieved,
        inherited_capabilities=inherited,
    )


def test_a_generation_without_inheritance_records_none() -> None:
    fitness = _challenger({"iron_backbone", "copper_mining"}, None)

    assert fitness.inherited_capabilities is None
    assert fitness.inherited_capabilities_source is None
    decision = compare_challenger(_incumbent(), fitness)
    assert any("new capabilities" in item for item in decision.improvements)


def test_a_resolved_inheritance_reaches_the_fitness_vector() -> None:
    fitness = _challenger(
        {"iron_backbone", "copper_mining"},
        InheritedCapabilities.resolved_as(
            {"copper_mining"},
            detail="run curriculum-1",
        ),
    )

    assert fitness.inherited_capabilities == frozenset({"copper_mining"})
    assert fitness.inherited_capabilities_source == "run curriculum-1"


def test_an_inherited_capability_is_not_credited_and_a_built_one_is() -> None:
    fitness = _challenger(
        {"iron_backbone", "copper_mining", "automation_science"},
        InheritedCapabilities.resolved_as(
            {"copper_mining"},
            detail="run curriculum-1",
        ),
    )

    decision = compare_challenger(_incumbent(), fitness)

    credited = [item for item in decision.improvements if "new capabilities" in item]
    assert credited, f"nada creditado: {decision.improvements}"
    assert "automation_science" in credited[0]
    assert "copper_mining" not in credited[0]


def test_an_empty_inheritance_still_credits_what_was_built() -> None:
    fitness = _challenger(
        {"iron_backbone", "copper_mining"},
        InheritedCapabilities.resolved_as(set(), detail="run curriculum-1"),
    )

    assert fitness.inherited_capabilities == frozenset()
    decision = compare_challenger(_incumbent(), fitness)
    assert any("new capabilities" in item for item in decision.improvements)


def test_an_unresolved_inheritance_never_becomes_none() -> None:
    fitness = _challenger(
        {"iron_backbone", "copper_mining"},
        InheritedCapabilities.unresolved("checkpoint meta carries no run_id"),
    )

    # Withholding, not crediting: the inheritance was applied and nobody could
    # say what came with it, so no capability is charged to this genome.
    assert fitness.inherited_capabilities is not None
    assert fitness.inherited_capabilities == frozenset(
        {"iron_backbone", "copper_mining"}
    )
    assert fitness.inherited_capabilities_source == (
        "checkpoint meta carries no run_id"
    )

    decision = compare_challenger(_incumbent(), fitness)
    assert not any("new capabilities" in item for item in decision.improvements)
    reported = " ".join(decision.incommensurable_metrics)
    assert "inherited_capabilities" in reported
    assert "checkpoint meta carries no run_id" in reported


def test_the_source_survives_a_round_trip() -> None:
    fitness = _challenger(
        {"iron_backbone"},
        InheritedCapabilities.resolved_as(
            {"iron_backbone"},
            detail="run curriculum-1",
        ),
    )

    restored = FitnessVector.from_dict(json.loads(json.dumps(fitness.to_dict())))

    assert restored.inherited_capabilities == frozenset({"iron_backbone"})
    assert restored.inherited_capabilities_source == "run curriculum-1"
    # A fitness recorded before the field existed has no inheritance in play.
    blank = FitnessVector.from_dict({"capabilities": []})
    assert blank.inherited_capabilities is None
    assert blank.inherited_capabilities_source is None


def _checkpoint(root: pathlib.Path, *, run_id: str | None) -> pathlib.Path:
    path = root / "lifelong_champion_state.json"
    path.write_text('{"entities": [], "inventories": []}\n', encoding="utf-8")
    meta: dict[str, Any] = {"path": str(path), "qualified": True}
    if run_id is not None:
        meta["run_id"] = run_id
    (root / "lifelong_champion_state.json.meta.json").write_text(
        json.dumps(meta) + "\n",
        encoding="utf-8",
    )
    return path


def _use_run_files(
    root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str | None = "curriculum-1",
) -> None:
    _checkpoint(root, run_id=run_id)
    monkeypatch.setattr(
        evolution_loop,
        "LIFELONG_CHECKPOINT",
        root / "lifelong_champion_state.json",
    )
    monkeypatch.setattr(
        evolution_loop,
        "EVOLUTION_CHAMPION",
        root / "evolution_champion.json",
    )
    monkeypatch.setattr(
        evolution_loop,
        "EVOLUTION_HISTORY",
        root / "evolution_history.jsonl",
    )


def test_the_capabilities_come_from_the_run_that_wrote_the_checkpoint(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_run_files(tmp_path, monkeypatch)
    (tmp_path / "evolution_champion.json").write_text(
        json.dumps(
            {
                "run_id": "curriculum-1",
                "generation": 37,
                "fitness": {"capabilities": ["copper_mining", "iron_backbone"]},
            }
        ),
        encoding="utf-8",
    )

    inherited = evolution_loop.resolve_inherited_capabilities()

    assert inherited.resolved is True
    assert inherited.capabilities == frozenset({"copper_mining", "iron_backbone"})
    assert "curriculum-1" in inherited.detail


def test_an_older_checkpoint_is_resolved_from_the_generation_history(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_run_files(tmp_path, monkeypatch)
    # The champion on disk is a later run: only the history still holds the
    # fitness of the run that wrote the checkpoint.
    (tmp_path / "evolution_champion.json").write_text(
        json.dumps(
            {
                "run_id": "curriculum-2",
                "fitness": {"capabilities": ["steam_power"]},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "evolution_history.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "run_id": "curriculum-0",
                    "challenger": {"fitness": {"capabilities": ["iron_backbone"]}},
                },
                {
                    "run_id": "curriculum-1",
                    "challenger": {
                        "fitness": {"capabilities": ["coal_mining", "iron_backbone"]}
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    inherited = evolution_loop.resolve_inherited_capabilities()

    assert inherited.resolved is True
    assert inherited.capabilities == frozenset({"coal_mining", "iron_backbone"})


def test_a_report_that_names_the_run_only_inside_the_challenger_resolves(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 12 of the 38 reports on disk predate the run_id being written at the top
    # level of the report; they name the run only in the challenger record.
    _use_run_files(tmp_path, monkeypatch)
    (tmp_path / "evolution_history.jsonl").write_text(
        json.dumps(
            {
                "generation": 6,
                "challenger": {
                    "run_id": "curriculum-1",
                    "fitness": {"capabilities": ["copper_smelting"]},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    inherited = evolution_loop.resolve_inherited_capabilities()

    assert inherited.resolved is True
    assert inherited.capabilities == frozenset({"copper_smelting"})


def test_a_checkpoint_without_a_run_id_is_unresolved(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_run_files(tmp_path, monkeypatch, run_id=None)

    inherited = evolution_loop.resolve_inherited_capabilities()

    assert inherited.resolved is False
    assert inherited.capabilities is None
    assert "run_id" in inherited.detail


def test_a_run_id_no_record_knows_is_unresolved(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_run_files(tmp_path, monkeypatch)
    (tmp_path / "evolution_champion.json").write_text(
        json.dumps({"run_id": "curriculum-9", "fitness": {"capabilities": []}}),
        encoding="utf-8",
    )

    inherited = evolution_loop.resolve_inherited_capabilities()

    assert inherited.resolved is False
    assert inherited.capabilities is None
    assert "curriculum-1" in inherited.detail


def test_a_record_without_a_capability_list_is_unresolved(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Absence is not emptiness: a fitness that never recorded capabilities
    # cannot answer what was inherited.
    _use_run_files(tmp_path, monkeypatch)
    (tmp_path / "evolution_champion.json").write_text(
        json.dumps({"run_id": "curriculum-1", "fitness": {}}),
        encoding="utf-8",
    )

    inherited = evolution_loop.resolve_inherited_capabilities()

    assert inherited.resolved is False
    assert inherited.capabilities is None


def test_an_unreadable_meta_is_unresolved(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_run_files(tmp_path, monkeypatch)
    (tmp_path / "lifelong_champion_state.json.meta.json").write_text(
        "{not json",
        encoding="utf-8",
    )

    inherited = evolution_loop.resolve_inherited_capabilities()

    assert inherited.resolved is False
    assert inherited.capabilities is None


def test_the_generation_report_declares_an_unresolved_inheritance(
    tmp_path: pathlib.Path,
) -> None:
    with sandboxed_runs(tmp_path):
        journal = curriculum_runner.ResearchJournal("curriculum-test")
        journal.state["metrics"] = {}
        journal.state["resource_accounting"] = {
            "exogenous_inputs": {"coal": {"status": "retired"}},
        }
        curriculum_runner.finalize_evolution_selection(
            journal,
            achieved={"iron_backbone"},
            physical_graph=None,
            inherited_capabilities=InheritedCapabilities.unresolved(
                "checkpoint meta carries no run_id"
            ),
        )
        report = json.loads(
            (tmp_path / "evolution_history.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )

    declared = report["inherited_capabilities"]
    assert declared["in_play"] is True
    assert declared["resolved"] is False
    assert declared["detail"] == "checkpoint meta carries no run_id"
    fitness = report["challenger"]["fitness"]
    assert fitness["inherited_capabilities"] == ["iron_backbone"]
    assert fitness["inherited_capabilities_source"] == (
        "checkpoint meta carries no run_id"
    )


def test_a_generation_with_no_inheritance_says_so_in_the_report(
    tmp_path: pathlib.Path,
) -> None:
    with sandboxed_runs(tmp_path):
        journal = curriculum_runner.ResearchJournal("curriculum-test")
        journal.state["metrics"] = {}
        journal.state["resource_accounting"] = {
            "exogenous_inputs": {"coal": {"status": "retired"}},
        }
        curriculum_runner.finalize_evolution_selection(
            journal,
            achieved={"iron_backbone"},
            physical_graph=None,
        )
        report = json.loads(
            (tmp_path / "evolution_history.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )

    assert report["inherited_capabilities"] == {"in_play": False}
    assert report["challenger"]["fitness"]["inherited_capabilities"] is None


def _function(source: str, name: str) -> ast.FunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} nao existe mais")


def _keyword(source: str, *, caller: str, callee: str, keyword: str) -> ast.AST:
    for node in ast.walk(_function(source, caller)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == callee:
            for entry in node.keywords:
                if entry.arg == keyword:
                    return entry.value
    raise AssertionError(f"{caller} nao passa {keyword} para {callee}")


def test_the_runner_hands_the_inheritance_to_the_selection() -> None:
    passed = _keyword(
        RUNNER_SOURCE,
        caller="run_curriculum",
        callee="finalize_evolution_selection",
        keyword="inherited_capabilities",
    )
    assert isinstance(passed, ast.Name), (
        "run_curriculum precisa repassar a heranca que recebeu"
    )


def test_the_loop_resolves_the_inheritance_and_hands_it_to_the_runner() -> None:
    passed = _keyword(
        LOOP_SOURCE,
        caller="run_loop",
        callee="run_curriculum",
        keyword="inherited_capabilities",
    )
    assert isinstance(passed, ast.Name), (
        "run_loop precisa passar a heranca resolvida ao runner"
    )
    resolved = [
        node
        for node in ast.walk(_function(LOOP_SOURCE, "run_loop"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_inherited_capabilities"
    ]
    assert resolved, "run_loop nao resolve as capacidades herdadas"
