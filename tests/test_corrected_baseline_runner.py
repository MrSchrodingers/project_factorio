from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "scripts" / "run_corrected_baseline_seed.py"
    spec = importlib.util.spec_from_file_location("run_corrected_baseline_seed", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _protocol(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "test_baseline_v1",
                "exploratory_seeds": [11],
                "confirmatory_seeds": [21],
            }
        )
        + "\n"
    )


def _release(path: Path) -> None:
    path.mkdir()
    (path / "BUILD_INFO.json").write_text(
        json.dumps(
            {
                "commit": "abc123456789",
                "branch": "research/cortex-v1",
                "dirty": False,
            }
        )
        + "\n"
    )


def test_plan_is_seed_isolated_and_uses_clean_release(tmp_path) -> None:
    module = _module()
    protocol = tmp_path / "protocol.json"
    release = tmp_path / "release"
    state = tmp_path / "state"
    _protocol(protocol)
    _release(release)

    plan = module.build_plan(
        seed=11,
        mode="exploratory",
        protocol_path=protocol,
        release_root=release,
        state_root=state,
    )

    assert plan["seed"] == 11
    assert plan["release"]["commit"] == "abc123456789"
    assert plan["sandbox_state_root"].endswith("/test_baseline_v1/exploratory/11")
    assert "--seed" in plan["command"]
    assert plan["execution_role"] == "baseline"
    role_index=plan["command"].index("--execution-role")
    assert plan["command"][role_index+1] == "baseline"


def test_seed_not_in_frozen_protocol_is_rejected(tmp_path) -> None:
    module = _module()
    protocol = tmp_path / "protocol.json"
    release = tmp_path / "release"
    _protocol(protocol)
    _release(release)

    with pytest.raises(ValueError, match="not frozen"):
        module.build_plan(
            seed=12,
            mode="exploratory",
            protocol_path=protocol,
            release_root=release,
            state_root=tmp_path / "state",
        )


def test_dirty_release_is_rejected(tmp_path) -> None:
    module = _module()
    protocol = tmp_path / "protocol.json"
    release = tmp_path / "release"
    _protocol(protocol)
    _release(release)
    build = json.loads((release / "BUILD_INFO.json").read_text())
    build["dirty"] = True
    (release / "BUILD_INFO.json").write_text(json.dumps(build))

    with pytest.raises(ValueError, match="clean build"):
        module.build_plan(
            seed=11,
            mode="exploratory",
            protocol_path=protocol,
            release_root=release,
            state_root=tmp_path / "state",
        )


def test_existing_seed_evidence_cannot_be_overwritten(tmp_path, monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "_evolution_active", lambda: False)
    sandbox = tmp_path / "state" / "baseline_runs" / "p" / "exploratory" / "11"
    sandbox.mkdir(parents=True)
    (sandbox / "manifest.json").write_text("{}\n")
    plan = {
        "sandbox_state_root": str(sandbox),
        "release_root": str(tmp_path / "release"),
        "command": ["false"],
    }

    with pytest.raises(FileExistsError, match="already has evidence"):
        module.run_plan(plan, state_root=tmp_path / "state")
