from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def _module():
    path=ROOT/"scripts"/"run_cortex_f5c_deterministic_baseline.py"
    spec=importlib.util.spec_from_file_location(
        "run_cortex_f5c_deterministic_baseline",
        path,
    )
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _revision(*,dirty: bool=False):
    return {
        "commit":"f5c-test-sha",
        "branch":"research/cortex-v1",
        "dirty":dirty,
        "source":"test",
    }


def _phase():
    return {
        "phase":"F5",
        "phase5_checkpoint":"F5-B",
        "phase5_next_checkpoint":"F5-C",
        "phase5_authority_bridge":{
            "validated":True,
            "ambient_authority":"A0",
            "max_bounded_grant":"A2",
            "max_executions":1,
            "continuous_authority":False,
            "world_mutation":False,
        },
    }


def _evolution_off():
    return {"active":"inactive","enabled":"disabled"}


def _write_manifest(path: Path,seed: int=1619515465) -> None:
    path.write_text(json.dumps({
        "seed_partitions":{"development":[seed]},
    })+"\n")


def _write_interventions(path: Path,rows=None) -> None:
    path.write_text(json.dumps({
        "schema_version":"cortex_f5_intervention_ledger_v1",
        "protocol_id":"cortex-f5-autonomous-factory-bootstrap-v1",
        "interventions":[] if rows is None else rows,
    })+"\n")


def test_preflight_f5c_is_read_only_and_bound_to_development_seed(
    tmp_path: Path,
) -> None:
    module=_module()
    manifest=tmp_path/"manifest.json"
    interventions=tmp_path/"interventions.json"
    _write_manifest(manifest)
    _write_interventions(interventions)
    artifact=tmp_path/"artifact.json"

    result=module.preflight_f5c(
        seed=1619515465,
        artifact=artifact,
        revision=_revision(),
        service_state_reader=_evolution_off,
        phase_state_reader=_phase,
        manifest_path=manifest,
        intervention_ledger_path=interventions,
        lease_state_path=tmp_path/"lease.json",
    )

    assert result["status"]=="preflight_pass"
    assert result["partition"]=="development"
    assert result["world_mutation"] is False
    assert result["world_reset"] is False
    assert result["grant_issued"] is False
    assert result["option_executed_live"] is False
    assert result["ambient_authority"]=="A0"
    assert result["max_bounded_grant"]=="A2"
    assert not artifact.exists()


def test_preflight_refuses_dirty_source(tmp_path: Path) -> None:
    module=_module()
    manifest=tmp_path/"manifest.json"
    interventions=tmp_path/"interventions.json"
    _write_manifest(manifest)
    _write_interventions(interventions)

    with pytest.raises(RuntimeError,match="clean committed"):
        module.preflight_f5c(
            seed=1619515465,
            artifact=tmp_path/"artifact.json",
            revision=_revision(dirty=True),
            service_state_reader=_evolution_off,
            phase_state_reader=_phase,
            manifest_path=manifest,
            intervention_ledger_path=interventions,
            lease_state_path=tmp_path/"lease.json",
        )


def test_preflight_refuses_seed_outside_development(tmp_path: Path) -> None:
    module=_module()
    manifest=tmp_path/"manifest.json"
    interventions=tmp_path/"interventions.json"
    _write_manifest(manifest)
    _write_interventions(interventions)

    with pytest.raises(ValueError,match="development partition"):
        module.preflight_f5c(
            seed=911399256,
            artifact=tmp_path/"artifact.json",
            revision=_revision(),
            service_state_reader=_evolution_off,
            phase_state_reader=_phase,
            manifest_path=manifest,
            intervention_ledger_path=interventions,
            lease_state_path=tmp_path/"lease.json",
        )


def test_preflight_refuses_active_world_lease(tmp_path: Path) -> None:
    module=_module()
    manifest=tmp_path/"manifest.json"
    interventions=tmp_path/"interventions.json"
    lease=tmp_path/"lease.json"
    _write_manifest(manifest)
    _write_interventions(interventions)
    lease.write_text(json.dumps({
        "status":"active",
        "run_id":"other",
    })+"\n")

    with pytest.raises(RuntimeError,match="active persisted WorldLease"):
        module.preflight_f5c(
            seed=1619515465,
            artifact=tmp_path/"artifact.json",
            revision=_revision(),
            service_state_reader=_evolution_off,
            phase_state_reader=_phase,
            manifest_path=manifest,
            intervention_ledger_path=interventions,
            lease_state_path=lease,
        )


def test_preflight_requires_zero_intervention_ledger(tmp_path: Path) -> None:
    module=_module()
    manifest=tmp_path/"manifest.json"
    interventions=tmp_path/"interventions.json"
    _write_manifest(manifest)
    _write_interventions(interventions,[{"kind":"human_action"}])

    with pytest.raises(RuntimeError,match="empty ledger"):
        module.preflight_f5c(
            seed=1619515465,
            artifact=tmp_path/"artifact.json",
            revision=_revision(),
            service_state_reader=_evolution_off,
            phase_state_reader=_phase,
            manifest_path=manifest,
            intervention_ledger_path=interventions,
            lease_state_path=tmp_path/"lease.json",
        )


def test_runner_has_exactly_one_a2_execution_and_no_legacy_runner_import() -> None:
    path=ROOT/"scripts"/"run_cortex_f5c_deterministic_baseline.py"
    source=path.read_text()
    tree=ast.parse(source)
    calls=0
    imports=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node,ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif (
            isinstance(node,ast.Call)
            and isinstance(node.func,ast.Attribute)
            and node.func.attr=="execute_a2"
        ):
            calls+=1

    assert calls==1
    assert "curriculum_runner" not in source
    assert "open_play_runner" not in source
    assert not any("curriculum_runner" in name for name in imports)
    assert not any("open_play_runner" in name for name in imports)
