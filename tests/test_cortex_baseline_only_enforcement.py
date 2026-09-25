from __future__ import annotations

import ast
from pathlib import Path

import pytest

from factorio_ai_lab.experiments import curriculum_runner

ROOT=Path(__file__).parents[1]


def _kwargs() -> dict[str, object]:
    return {
        "seed":1,
        "placement_episodes":1,
        "baseline_settle":1,
        "trial_settle":1,
        "scale_settle":1,
        "smelt_settle":1,
        "logistics_settle":1,
        "belt_smelt_settle":1,
        "coal_mine_settle":1,
        "copper_mine_settle":1,
        "copper_smelt_settle":1,
        "exploration":1.0,
    }


def test_legacy_runner_refuses_non_baseline_before_environment_creation(monkeypatch) -> None:
    called=False

    def _should_not_run() -> None:
        nonlocal called
        called=True

    monkeypatch.setattr(curriculum_runner,"list_environments",_should_not_run)
    with pytest.raises(curriculum_runner.LegacyRunnerAuthorityError,match="legacy_runner_baseline_only"):
        curriculum_runner.run_curriculum(execution_role="cortex",**_kwargs())
    assert called is False


def test_exact_baseline_role_is_the_only_allowed_role() -> None:
    curriculum_runner.require_legacy_baseline_role("baseline")
    for role in ("cortex","execute","BASELINE","",None):
        with pytest.raises(curriculum_runner.LegacyRunnerAuthorityError):
            curriculum_runner.require_legacy_baseline_role(role)  # type: ignore[arg-type]


def test_cortex_package_cannot_import_curriculum_runner() -> None:
    offenders=[]
    for path in sorted((ROOT/"src/factorio_ai_lab/cortex").rglob("*.py")):
        tree=ast.parse(path.read_text(),filename=str(path))
        for node in ast.walk(tree):
            modules=[]
            if isinstance(node,ast.Import):
                modules=[alias.name for alias in node.names]
            elif isinstance(node,ast.ImportFrom):
                modules=[node.module or ""]
            if any("curriculum_runner" in module for module in modules):
                offenders.append(str(path.relative_to(ROOT)))
                break
    assert offenders == []


def test_every_production_callsite_labels_legacy_runner_as_baseline() -> None:
    evolution=ast.parse((ROOT/"src/factorio_ai_lab/experiments/evolution_loop.py").read_text())
    calls=[]
    for node in ast.walk(evolution):
        if isinstance(node,ast.Call):
            target=node.func
            name=target.id if isinstance(target,ast.Name) else None
            if name=="run_curriculum":
                calls.append({kw.arg for kw in node.keywords if kw.arg})
    assert calls
    assert all("execution_role" in keywords for keywords in calls)

    baseline=(ROOT/"scripts/run_corrected_baseline_seed.py").read_text()
    shell=(ROOT/"scripts/run_curriculum.sh").read_text()
    assert '"--execution-role"' in baseline
    assert '"baseline"' in baseline
    assert "--execution-role baseline" in shell
