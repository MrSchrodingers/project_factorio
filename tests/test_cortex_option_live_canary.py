from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from factorio_ai_lab.cortex.actions import ActionAuthority
from factorio_ai_lab.cortex.live_canary import validate_canary_seed
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND


def _module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_cortex_option_live_canary.py"
    )
    spec = importlib.util.spec_from_file_location(
        "run_cortex_option_live_canary",
        path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _revision(*, dirty: bool = False):
    return {
        "commit": "g4b-test-sha",
        "branch": "research/cortex-v1",
        "dirty": dirty,
        "source": "test",
    }


def _evolution_off():
    return {"active": "inactive", "enabled": "disabled"}


def _g4a_phase_state():
    return {
        "phase": "F2",
        "phase2_checkpoint": "F2-G4A",
        "phase2_persistent_option_authority": {
            "validated": True,
            "world_mutation": False,
            "continuous_authority": False,
            "live_option_execute_authorized": False,
            "dry_run_run_id": "g4a-dry-run",
            "dry_run_code_commit": "g4a-implementation-sha",
        },
    }


def test_preflight_is_read_only_and_does_not_create_artifact(
    tmp_path: Path,
) -> None:
    module = _module()
    artifact = tmp_path / "g4b.json"

    result = module.preflight_live_canary(
        seed=424242,
        artifact=artifact,
        revision=_revision(),
        service_state_reader=_evolution_off,
        phase_state_reader=_g4a_phase_state,
        lease_state_path=tmp_path / "lease.json",
    )

    assert result["status"] == "preflight_pass"
    assert result["world_mutation"] is False
    assert result["grant_issued"] is False
    assert result["continuous_authority"] is False
    assert result["live_option_execute_authorized"] is False
    assert not artifact.exists()


def test_preflight_refuses_dirty_source(tmp_path: Path) -> None:
    module = _module()

    with pytest.raises(RuntimeError, match="clean committed"):
        module.preflight_live_canary(
            seed=424242,
            artifact=tmp_path / "g4b.json",
            revision=_revision(dirty=True),
            service_state_reader=_evolution_off,
            lease_state_path=tmp_path / "lease.json",
        )


@pytest.mark.parametrize(
    ("active", "enabled"),
    [
        ("active", "disabled"),
        ("inactive", "enabled"),
        ("active", "enabled"),
    ],
)
def test_preflight_requires_evolution_inactive_and_disabled(
    tmp_path: Path,
    active: str,
    enabled: str,
) -> None:
    module = _module()

    with pytest.raises(RuntimeError, match="inactive\\+disabled"):
        module.preflight_live_canary(
            seed=424242,
            artifact=tmp_path / "g4b.json",
            revision=_revision(),
            service_state_reader=lambda: {
                "active": active,
                "enabled": enabled,
            },
            phase_state_reader=_g4a_phase_state,
            lease_state_path=tmp_path / "lease.json",
        )


def test_preflight_refuses_existing_canonical_artifact(
    tmp_path: Path,
) -> None:
    module = _module()
    artifact = tmp_path / "g4b.json"
    artifact.write_text("{}\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        module.preflight_live_canary(
            seed=424242,
            artifact=artifact,
            revision=_revision(),
            service_state_reader=_evolution_off,
            lease_state_path=tmp_path / "lease.json",
        )




def test_preflight_refuses_active_persisted_world_lease(
    tmp_path: Path,
) -> None:
    module = _module()
    lease = tmp_path / "lease.json"
    lease.write_text(
        '{"status":"active","run_id":"other-run","arena":"open_play"}\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="active persisted"):
        module.preflight_live_canary(
            seed=424242,
            artifact=tmp_path / "g4b.json",
            revision=_revision(),
            service_state_reader=_evolution_off,
            lease_state_path=lease,
        )


@pytest.mark.parametrize(
    ("bootstrap", "structural", "ttl"),
    [
        (0, 10, 300),
        (-1, 10, 300),
        (12, 0, 300),
        (12, -1, 300),
        (12, 10, 0),
        (12, 10, -1),
    ],
)
def test_timing_and_ttl_parameters_fail_before_live_setup(
    bootstrap: int,
    structural: int,
    ttl: int,
) -> None:
    module = _module()

    with pytest.raises(ValueError, match="positive integer"):
        module.validate_canary_parameters(
            bootstrap_settle_seconds=bootstrap,
            structural_settle_seconds=structural,
            grant_ttl_seconds=ttl,
        )


def test_confirmatory_seed_is_refused() -> None:
    with pytest.raises(ValueError, match="reserved for confirmatory"):
        validate_canary_seed(20261101)


def test_option_request_is_shadow_and_budget_is_factorio_ticks() -> None:
    module = _module()

    request = module._build_option_request(
        run_id="g4b-run",
        commit="g4b-sha",
        structural_settle_seconds=10,
    )

    assert request.authority is ActionAuthority.SHADOW
    assert request.budget.requested_ticks == 10 * int(TICKS_PER_SECOND)
    assert request.provenance.run_id == "g4b-run"
    assert request.provenance.code_revision == "g4b-sha"


def test_runner_uses_generic_option_boundary_once_without_legacy_runner() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "run_cortex_option_live_canary.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported: set[str] = set()
    boundary_execute_calls = 0
    module_level_gym_imports = sum(
        1
        for node in tree.body
        if isinstance(node, ast.Import)
        and any(alias.name == "gym" for alias in node.names)
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "boundary"
            and node.func.attr == "execute"
        ):
            boundary_execute_calls += 1

    assert "curriculum_runner" not in source
    assert not any("curriculum_runner" in name for name in imported)
    assert "OptionExecutionBoundary" in source
    assert boundary_execute_calls == 1
    assert "StructuralTransactionalAdapter" not in source
    assert module_level_gym_imports == 0


def test_live_canary_helper_module_is_read_only_instrumentation() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "src" / "factorio_ai_lab" / "cortex" / "live_canary.py"
    source = path.read_text(encoding="utf-8")

    assert "TransactionalFLEExecutor" not in source
    assert "OptionExecutionBoundary" not in source
    assert "curriculum_runner" not in source


def test_preflight_requires_validated_g4a_phase_state(
    tmp_path: Path,
) -> None:
    module = _module()
    invalid = _g4a_phase_state()
    invalid["phase2_checkpoint"] = "F2-G3"

    with pytest.raises(RuntimeError, match="validated F2-G4A"):
        module.preflight_live_canary(
            seed=424242,
            artifact=tmp_path / "g4b.json",
            revision=_revision(),
            service_state_reader=_evolution_off,
            phase_state_reader=lambda: invalid,
            lease_state_path=tmp_path / "lease.json",
        )
