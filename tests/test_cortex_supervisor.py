from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _module():
    path=ROOT/"scripts"/"run_cortex_supervisor.py"
    spec=importlib.util.spec_from_file_location("run_cortex_supervisor",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frontier_returns_first_unpromoted_capability() -> None:
    module=_module()
    state={
        "phase5_protocol":{
            "capabilities":["iron_extraction","coal_self_sufficiency"],
            "achieved_capabilities":[],
        },
    }
    assert module.frontier(state)=="iron_extraction"


def test_supervisor_dispatch_is_explicit_and_bounded() -> None:
    module=_module()
    assert module.RUNNERS=={
        "iron_extraction":"run_cortex_f5c_deterministic_baseline.py",
        "coal_self_sufficiency":"run_cortex_f5c_coal_self_sufficiency.py",
        "iron_smelting":"run_cortex_f5c_iron_smelting.py",
        "steam_power":"run_cortex_f5c_steam_power.py",
        "copper_chain":"run_cortex_f5c_copper_chain.py",
        "automation_science":"run_cortex_f5c_automation_science.py",
        "powered_manufacturing":"run_cortex_f5c_powered_manufacturing.py",
    }


def test_preflight_block_is_not_terminal_counterexample() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()

    assert 'last_result in {"failed","rejected"}' in source
    assert '"preflight_blocked"' in source
    assert 'last_result="blocked"' not in source


def test_runner_subprocess_is_pinned_to_supervisor_release() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()

    assert 'CODE_ROOT=Path(__file__).resolve().parents[1]' in source
    assert 'env["PYTHONPATH"]=str(CODE_ROOT/"src")' in source
    assert 'str(CODE_ROOT/"scripts"/script)' in source
    assert "cwd=STATE_ROOT" in source
    assert "env=env" in source


def test_coal_preflight_can_repair_missing_iron() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()

    assert '"promoted iron entities are absent from live WORLD"' in source
    assert 'payload["repair_frontier"]="iron_extraction"' in source
    assert '"repairing_prerequisite_execute"' in source


def test_cortex_service_executes_versioned_runtime_release() -> None:
    unit=(ROOT/"ops"/"systemd"/"factorio-ai-cortex.service").read_text()

    assert (
        "ExecStart=/srv/factorio-ai-lab/.venv-fle/bin/python "
        "/srv/factorio-ai-runtime/current/scripts/run_cortex_supervisor.py"
        in unit
    )
    assert "/usr/local/lib/factorio-ai/cortex_supervisor.py" not in unit


def test_release_commit_prefers_immutable_build_info(
    tmp_path: Path,
) -> None:
    module=_module()
    module.CODE_ROOT=tmp_path
    (tmp_path/"BUILD_INFO.json").write_text(
        '{"commit":"abc123","dirty":false,"branch":"research/cortex-v1"}\n'
    )

    assert module.release_commit()=="abc123"


def test_supervisor_separates_code_release_from_mutable_state() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()

    assert 'STATE_ROOT=Path(' in source
    assert 'os.environ.get("FACTORIO_AI_STATE_ROOT","/srv/factorio-ai-lab")' in source
    assert 'str(CODE_ROOT/"scripts/cortex_phase_state.py")' in source
    assert '"--state-root",str(STATE_ROOT)' in source
    assert '"--protocol",str(CODE_ROOT/"configs/cortex_baseline_v1.json")' in source
    assert '"code_root":str(CODE_ROOT)' in source
    assert '"state_root":str(STATE_ROOT)' in source
