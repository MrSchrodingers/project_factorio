from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _module(name: str):
    path=ROOT/"scripts"/name
    spec=importlib.util.spec_from_file_location(name.replace(".py",""),path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release(path: Path, commit: str="abc") -> None:
    path.mkdir()
    (path/"scripts").mkdir()
    (path/"scripts"/"run_corrected_baseline_seed.py").write_text("# runner\n")
    (path/"BUILD_INFO.json").write_text(json.dumps({
        "commit":commit,
        "dirty":False,
        "branch":"research/cortex-v1",
    })+"\n")


def _protocol(path: Path) -> None:
    path.write_text(json.dumps({
        "schema_version":"p1",
        "scientific_release_commit":"abc",
        "scientific_release_root":str(path.parent/"release"),
        "exploratory_seeds":[11,12],
        "confirmatory_seeds":[21],
    })+"\n")


def test_detached_launch_plan_pins_exact_release(tmp_path) -> None:
    module=_module("launch_corrected_baseline_seed.py")
    release=tmp_path/"release"
    protocol=tmp_path/"protocol.json"
    state=tmp_path/"state"
    (state/".venv-fle"/"bin").mkdir(parents=True)
    _release(release)
    _protocol(protocol)

    plan=module.build_launch_plan(
        seed=11,
        mode="exploratory",
        release_root=release,
        expected_commit="abc",
        state_root=state,
        protocol_path=protocol,
    )

    assert plan["expected_commit"]=="abc"
    assert plan["scientific_release"]["commit"]=="abc"
    assert str(release/"scripts"/"run_corrected_baseline_seed.py") in plan["command"]


def test_storage_headroom_reports_space_and_rejects_impossible_threshold(tmp_path) -> None:
    module=_module("launch_corrected_baseline_seed.py")

    measured=module.storage_headroom(tmp_path,minimum_free_bytes=1)
    assert measured["path"] == str(tmp_path)
    assert measured["free_bytes"] >= 1
    assert measured["minimum_free_bytes"] == 1

    try:
        module.storage_headroom(
            tmp_path,
            minimum_free_bytes=measured["total_bytes"] + 1,
        )
    except RuntimeError as exc:
        assert "insufficient free space" in str(exc)
    else:
        raise AssertionError("storage gate accepted impossible free-space threshold")


def test_isolation_snapshot_captures_global_hashes_and_refuses_champion(tmp_path) -> None:
    module=_module("launch_corrected_baseline_seed.py")
    runs=tmp_path/"runs"
    runs.mkdir()
    (runs/"knowledge.jsonl").write_text('{"lesson":"x"}\n')

    snapshot=module.capture_global_isolation_snapshot(
        state_root=tmp_path,
        seed=11,
    )
    payload=json.loads(snapshot.read_text())

    assert payload["seed"]==11
    assert payload["evolution_champion_exists"] is False
    assert payload["files"]["knowledge.jsonl"]["exists"] is True
    assert payload["files"]["knowledge.jsonl"]["sha256"]

    snapshot.unlink()
    (runs/"evolution_champion.json").write_text("{}\n")
    try:
        module.capture_global_isolation_snapshot(
            state_root=tmp_path,
            seed=11,
        )
    except RuntimeError as exc:
        assert "evolution_champion" in str(exc)
    else:
        raise AssertionError("launcher accepted a global champion during F1-B")


def test_detached_launch_plan_rejects_concurrent_baseline_seed(tmp_path) -> None:
    module=_module("launch_corrected_baseline_seed.py")
    release=tmp_path/"release"
    protocol=tmp_path/"protocol.json"
    state=tmp_path/"state"
    _release(release)
    _protocol(protocol)

    running=state/"baseline_runs"/"p1"/"exploratory"/"11"
    running.mkdir(parents=True)
    (running/"manifest.json").write_text(json.dumps({
        "status":"running",
        "mode":"exploratory",
        "seed":11,
        "release":{"commit":"abc"},
    })+"\n")

    try:
        module.build_launch_plan(
            seed=12,
            mode="exploratory",
            release_root=release,
            expected_commit="abc",
            state_root=state,
            protocol_path=protocol,
        )
    except RuntimeError as exc:
        assert "concurrent baseline launch" in str(exc)
        assert "exploratory:11" in str(exc)
    else:
        raise AssertionError("concurrent baseline seed was accepted")


def test_detached_launch_plan_rejects_wrong_release(tmp_path) -> None:
    module=_module("launch_corrected_baseline_seed.py")
    release=tmp_path/"release"
    protocol=tmp_path/"protocol.json"
    state=tmp_path/"state"
    _release(release)
    _protocol(protocol)

    try:
        module.build_launch_plan(
            seed=11,
            mode="exploratory",
            release_root=release,
            expected_commit="wrong",
            state_root=state,
            protocol_path=protocol,
        )
    except ValueError as exc:
        assert "scientific release commit abc != expected wrong" in str(exc)
    else:
        raise AssertionError("wrong scientific release was accepted")


def test_phase_state_finds_next_seed_and_validates_commit(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)
    seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/"11"
    seed_dir.mkdir(parents=True)
    (seed_dir/"manifest.json").write_text(json.dumps({
        "status":"completed",
        "returncode":0,
        "release":{"commit":"abc"},
        "finished_at":"2026-01-01T00:00:00+00:00",
    })+"\n")
    (seed_dir/"result.json").write_text(json.dumps({
        "code_revision":{"commit":"abc","dirty":False},
        "completed_stage_count":14,
        "bottleneck":"Logistic science",
        "challenger":{
            "run_id":"run-11",
            "fitness":{"autonomy_score":0.5,"closed_loop_autonomy":False},
        },
    })+"\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["modes"]["exploratory"]["valid_completed"]==1
    assert state["modes"]["exploratory"]["next_seed"]==12
    assert state["resume"]["action"]=="run seed 12"
    assert state["baseline_release_consistent"] is True


def test_phase_state_never_advances_past_running_seed(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)
    seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/"11"
    seed_dir.mkdir(parents=True)
    (seed_dir/"manifest.json").write_text(json.dumps({
        "status":"running",
        "release":{"commit":"abc"},
        "started_at":"2026-01-01T00:00:00+00:00",
    })+"\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["modes"]["exploratory"]["running"]==[11]
    assert state["modes"]["exploratory"]["next_seed"] is None
    assert state["resume"]["do_not_start_another_seed"] is True


def test_phase_state_blocks_next_seed_when_completed_record_is_invalid(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)
    seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/"11"
    seed_dir.mkdir(parents=True)
    (seed_dir/"manifest.json").write_text(json.dumps({
        "status":"completed",
        "returncode":0,
        "release":{"commit":"abc"},
    })+"\n")
    (seed_dir/"result.json").write_text(json.dumps({
        "code_revision":{"commit":"different","dirty":False},
        "challenger":{"fitness":{}},
    })+"\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["modes"]["exploratory"]["invalid"]==[11]
    assert state["resume"]["do_not_start_another_seed"] is True
    assert state["resume"]["action"].startswith("investigate invalid seed")


def test_phase_state_blocks_release_mismatch_even_when_seed_is_valid(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)
    seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/"11"
    seed_dir.mkdir(parents=True)
    (seed_dir/"manifest.json").write_text(json.dumps({
        "status":"completed",
        "returncode":0,
        "release":{"commit":"other"},
    })+"\n")
    (seed_dir/"result.json").write_text(json.dumps({
        "code_revision":{"commit":"other","dirty":False},
        "challenger":{"fitness":{}},
    })+"\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["modes"]["exploratory"]["valid_completed"]==1
    assert state["release_mismatches"]==["other"]
    assert state["baseline_release_consistent"] is False
    assert state["resume"]["do_not_start_another_seed"] is True
    assert state["resume"]["action"]=="halt: scientific release provenance mismatch"

def test_phase_state_marks_f1_complete_when_exploratory_series_and_report_exist(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)

    for seed in (11,12):
        seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/str(seed)
        seed_dir.mkdir(parents=True)
        (seed_dir/"manifest.json").write_text(json.dumps({
            "status":"completed",
            "returncode":0,
            "release":{"commit":"abc"},
            "finished_at":"2026-01-01T00:00:00+00:00",
        })+"\n")
        (seed_dir/"result.json").write_text(json.dumps({
            "code_revision":{"commit":"abc","dirty":False},
            "challenger":{
                "run_id":f"run-{seed}",
                "fitness":{"autonomy_score":0.5,"closed_loop_autonomy":False},
            },
        })+"\n")

    docs=tmp_path/"docs"
    docs.mkdir()
    (docs/"CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md").write_text("# report\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["exploratory_complete"] is True
    assert state["statistical_report"]["exists"] is True
    assert state["phase"] == "F1"
    assert state["phase_status"] == "complete"
    assert state["resume"]["action"] == "F1 complete; follow docs/CORTEX_HANDOFF.md"

def test_phase_state_marks_f2_active_when_action_ontology_exists(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)

    for seed in (11,12):
        seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/str(seed)
        seed_dir.mkdir(parents=True)
        (seed_dir/"manifest.json").write_text(json.dumps({
            "status":"completed",
            "returncode":0,
            "release":{"commit":"abc"},
        })+"\n")
        (seed_dir/"result.json").write_text(json.dumps({
            "code_revision":{"commit":"abc","dirty":False},
            "challenger":{"fitness":{}},
        })+"\n")

    docs=tmp_path/"docs"
    docs.mkdir()
    (docs/"CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md").write_text("# report\n")
    (docs/"CORTEX_PHASE2_ACTION_ONTOLOGY.md").write_text("# F2\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["phase"] == "F2"
    assert state["phase_status"] == "active"
    assert state["phase2_ontology"]["exists"] is True
    assert state["resume"]["action"] == "F2 active; follow docs/CORTEX_HANDOFF.md"

def test_phase_state_marks_f2b_when_legacy_parity_document_exists(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)

    for seed in (11,12):
        seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/str(seed)
        seed_dir.mkdir(parents=True)
        (seed_dir/"manifest.json").write_text(json.dumps({
            "status":"completed",
            "returncode":0,
            "release":{"commit":"abc"},
        })+"\n")
        (seed_dir/"result.json").write_text(json.dumps({
            "code_revision":{"commit":"abc","dirty":False},
            "challenger":{"fitness":{}},
        })+"\n")

    docs=tmp_path/"docs"
    docs.mkdir()
    (docs/"CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md").write_text("# report\n")
    (docs/"CORTEX_PHASE2_ACTION_ONTOLOGY.md").write_text("# F2-A\n")
    (docs/"CORTEX_PHASE2_LEGACY_PARITY.md").write_text("# F2-B\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["phase"] == "F2"
    assert state["phase_status"] == "active"
    assert state["phase2_checkpoint"] == "F2-B"
    assert state["phase2_parity"]["exists"] is True
    assert state["resume"]["action"] == "F2 active; follow docs/CORTEX_HANDOFF.md"

def test_phase_state_marks_f2c_when_structural_planning_document_exists(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)

    for seed in (11,12):
        seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/str(seed)
        seed_dir.mkdir(parents=True)
        (seed_dir/"manifest.json").write_text(json.dumps({
            "status":"completed",
            "returncode":0,
            "release":{"commit":"abc"},
        })+"\n")
        (seed_dir/"result.json").write_text(json.dumps({
            "code_revision":{"commit":"abc","dirty":False},
            "challenger":{"fitness":{}},
        })+"\n")

    docs=tmp_path/"docs"
    docs.mkdir()
    (docs/"CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md").write_text("# report\n")
    (docs/"CORTEX_PHASE2_ACTION_ONTOLOGY.md").write_text("# F2-A\n")
    (docs/"CORTEX_PHASE2_LEGACY_PARITY.md").write_text("# F2-B\n")
    (docs/"CORTEX_PHASE2_STRUCTURAL_PLANNING.md").write_text("# F2-C\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["phase"] == "F2"
    assert state["phase_status"] == "active"
    assert state["phase2_checkpoint"] == "F2-C"
    assert state["phase2_structural"]["exists"] is True
    assert state["resume"]["action"] == "F2 active; follow docs/CORTEX_HANDOFF.md"

def test_phase_state_marks_f2d_when_structural_preparation_document_exists(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)

    for seed in (11,12):
        seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/str(seed)
        seed_dir.mkdir(parents=True)
        (seed_dir/"manifest.json").write_text(json.dumps({
            "status":"completed",
            "returncode":0,
            "release":{"commit":"abc"},
        })+"\n")
        (seed_dir/"result.json").write_text(json.dumps({
            "code_revision":{"commit":"abc","dirty":False},
            "challenger":{"fitness":{}},
        })+"\n")

    docs=tmp_path/"docs"
    docs.mkdir()
    (docs/"CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md").write_text("# report\n")
    (docs/"CORTEX_PHASE2_ACTION_ONTOLOGY.md").write_text("# F2-A\n")
    (docs/"CORTEX_PHASE2_LEGACY_PARITY.md").write_text("# F2-B\n")
    (docs/"CORTEX_PHASE2_STRUCTURAL_PLANNING.md").write_text("# F2-C\n")
    (docs/"CORTEX_PHASE2_STRUCTURAL_PREPARATION.md").write_text("# F2-D\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["phase"] == "F2"
    assert state["phase_status"] == "active"
    assert state["phase2_checkpoint"] == "F2-D"
    assert state["phase2_preparation"]["exists"] is True
    assert state["resume"]["action"] == "F2 active; follow docs/CORTEX_HANDOFF.md"

def test_phase_state_tracks_f2e1_and_f2e2_from_persisted_artifacts(tmp_path) -> None:
    module=_module("cortex_phase_state.py")
    protocol=tmp_path/"protocol.json"
    _protocol(protocol)

    for seed in (11,12):
        seed_dir=tmp_path/"baseline_runs"/"p1"/"exploratory"/str(seed)
        seed_dir.mkdir(parents=True)
        (seed_dir/"manifest.json").write_text(json.dumps({
            "status":"completed",
            "returncode":0,
            "release":{"commit":"abc"},
        })+"\n")
        (seed_dir/"result.json").write_text(json.dumps({
            "code_revision":{"commit":"abc","dirty":False},
            "challenger":{"fitness":{}},
        })+"\n")

    docs=tmp_path/"docs"
    docs.mkdir()
    for name in (
        "CORTEX_PHASE1_BASELINE_STATISTICAL_REPORT.md",
        "CORTEX_PHASE2_ACTION_ONTOLOGY.md",
        "CORTEX_PHASE2_LEGACY_PARITY.md",
        "CORTEX_PHASE2_STRUCTURAL_PLANNING.md",
        "CORTEX_PHASE2_STRUCTURAL_PREPARATION.md",
        "CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md",
    ):
        (docs/name).write_text("# checkpoint\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)
    assert state["phase2_checkpoint"] == "F2-E1"
    assert state["phase2_execution"]["exists"] is True
    assert state["phase2_canary"]["exists"] is False

    audit=tmp_path/"runs"/"audits"
    audit.mkdir(parents=True)
    (audit/"cortex_f2e_structural_canary.json").write_text("{}\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)
    assert state["phase2_checkpoint"] == "F2-E2"
    assert state["phase2_canary"]["exists"] is True
