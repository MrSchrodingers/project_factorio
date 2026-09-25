from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
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

def test_phase_state_exposes_f2e_canary_outcome(tmp_path) -> None:
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
        "CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md",
    ):
        (docs/name).write_text("# checkpoint\n")

    audit=tmp_path/"runs"/"audits"
    audit.mkdir(parents=True)
    (audit/"cortex_f2e_structural_canary.json").write_text(json.dumps({
        "status":"completed",
        "run_id":"canary-1",
        "transaction_committed":False,
        "rollback_observed":True,
        "action_result":{
            "status":"rejected",
            "refusal":{"code":"structural_postcondition_failed"},
            "measurements":{
                "candidate_after":{
                    "processor_status":"no_fuel",
                    "processor_output":0.0,
                },
            },
        },
    })+"\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)
    canary=state["phase2_canary"]

    assert state["phase2_checkpoint"] == "F2-E2"
    assert canary["status"] == "completed"
    assert canary["run_id"] == "canary-1"
    assert canary["action_status"] == "rejected"
    assert canary["refusal"] == "structural_postcondition_failed"
    assert canary["classification"] == "functional_dependency_missing"
    assert canary["processor_status"] == "no_fuel"
    assert canary["processor_output"] == 0.0
    assert canary["transaction_committed"] is False
    assert canary["rollback_observed"] is True

def test_phase_state_exposes_f2e_candidate_and_rollback_measurements(tmp_path) -> None:
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
        "CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md",
    ):
        (docs/name).write_text("# checkpoint\n")

    before={
        "producers_reaching_processor":0,
        "physical_processing_coverage":0.0,
        "processor_exists":False,
        "processor_status":None,
        "processor_output":0.0,
    }
    candidate={
        "producers_reaching_processor":1,
        "physical_processing_coverage":1.0,
        "processor_exists":True,
        "processor_status":"no_fuel",
        "processor_output":0.0,
    }
    audit=tmp_path/"runs"/"audits"
    audit.mkdir(parents=True)
    (audit/"cortex_f2e_structural_canary.json").write_text(json.dumps({
        "status":"completed",
        "run_id":"canary-2",
        "authority":"execute",
        "continuous_authority":False,
        "code_revision":{"commit":"deadbeef","dirty":False},
        "transaction_committed":False,
        "rollback_observed":True,
        "measurement_before":before,
        "measurement_final":before,
        "action_result":{
            "status":"rejected",
            "refusal":{"code":"structural_postcondition_failed"},
            "measurements":{"candidate_after":candidate},
            "postconditions":[
                {
                    "name":"processor_output",
                    "operator":"increase",
                    "state":"unsatisfied",
                    "hard":True,
                },
            ],
        },
    })+"\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)
    canary=state["phase2_canary"]

    assert canary["authority"] == "execute"
    assert canary["continuous_authority"] is False
    assert canary["code_commit"] == "deadbeef"
    assert canary["candidate_after"]["processor_status"] == "no_fuel"
    assert canary["candidate_after"]["physical_processing_coverage"] == 1.0
    assert canary["measurement_final"] == before
    assert canary["postconditions"][0]["state"] == "unsatisfied"

def test_phase_state_advances_to_f2f1_when_functional_dependency_doc_exists(tmp_path) -> None:
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
        "CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md",
        "CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY.md",
    ):
        (docs/name).write_text("# checkpoint\n")

    audit=tmp_path/"runs"/"audits"
    audit.mkdir(parents=True)
    (audit/"cortex_f2e_structural_canary.json").write_text(json.dumps({
        "status":"completed",
        "action_result":{"status":"rejected"},
        "rollback_observed":True,
    })+"\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["phase2_checkpoint"] == "F2-F1"
    assert state["phase2_functional"]["exists"] is True
    assert state["phase2_canary"]["exists"] is True
    assert state["resume"]["action"] == "F2 active; follow docs/CORTEX_HANDOFF.md"

def test_phase_state_advances_from_f2f1_to_f2f2_when_composition_doc_exists(tmp_path) -> None:
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
        "CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md",
        "CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY.md",
    ):
        (docs/name).write_text("# checkpoint\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)
    assert state["phase2_checkpoint"] == "F2-F1"
    assert state["phase2_functional_composition"]["exists"] is False

    (docs/"CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY_COMPOSITION.md").write_text(
        "# F2-F2\n"
    )
    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)

    assert state["phase2_checkpoint"] == "F2-F2"
    assert state["phase2_functional_composition"]["exists"] is True

def test_phase_state_advances_to_f2f3_and_exposes_functional_canary(tmp_path) -> None:
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
        "CORTEX_PHASE2_TRANSACTIONAL_EXECUTION.md",
        "CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY.md",
        "CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY_COMPOSITION.md",
    ):
        (docs/name).write_text("# checkpoint\n")

    audit=tmp_path/"runs"/"audits"
    audit.mkdir(parents=True)
    (audit/"cortex_f2f_structural_canary.json").write_text(json.dumps({
        "status":"completed",
        "run_id":"f2f3-canary",
        "authority":"execute",
        "continuous_authority":False,
        "code_revision":{"commit":"feedface","dirty":False},
        "transaction_committed":False,
        "rollback_observed":True,
        "functional_dependency":{
            "ready":True,
            "dependency":{
                "fuel":{"name":"coal"},
                "units_needed":1,
                "carried_only":True,
            },
        },
        "measurement_before":{
            "producers_reaching_processor":0,
            "processor_output":0.0,
        },
        "measurement_final":{
            "producers_reaching_processor":0,
            "processor_output":0.0,
        },
        "action_result":{
            "status":"rejected",
            "refusal":{"code":"structural_postcondition_failed"},
            "measurements":{
                "candidate_after":{
                    "producers_reaching_processor":1,
                    "physical_processing_coverage":1.0,
                    "processor_exists":True,
                    "processor_status":"no_ingredients",
                    "processor_output":0.0,
                },
            },
            "postconditions":[],
        },
    })+"\n")

    state=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)
    canary=state["phase2_functional_canary"]

    assert state["phase2_checkpoint"] == "F2-F3"
    assert canary["exists"] is True
    assert canary["classification"] == "processor_input_missing_with_rollback"
    assert canary["code_commit"] == "feedface"
    assert canary["processor_status"] == "no_ingredients"
    assert canary["processor_output"] == 0.0
    assert canary["functional_dependency_ready"] is True
    assert canary["fuel"] == "coal"
    assert canary["fuel_units"] == 1
    assert canary["fuel_carried_only"] is True
    assert canary["transaction_committed"] is False
    assert canary["rollback_observed"] is True

    (docs/"CORTEX_PHASE2_DELIVERY_ACTUATOR_DEPENDENCY.md").write_text(
        "# F2-F4\n"
    )
    f2f4=module.build_phase_state(state_root=tmp_path,protocol_path=protocol)
    assert f2f4["phase2_checkpoint"] == "F2-F4A"
    assert f2f4["phase2_delivery_actuator"]["exists"] is True
    assert f2f4["phase2_delivery_actuator_runner"]["exists"] is False
    assert (
        f2f4["phase2_functional_canary"]["classification"]
        == "processor_input_missing_with_rollback"
    )


    (docs/"CORTEX_PHASE2_DELIVERY_ACTUATOR_RUNNER.md").write_text(
        "# F2-F4B\n"
    )
    f2f4b=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f2f4b["phase2_checkpoint"] == "F2-F4B"
    assert f2f4b["phase2_delivery_actuator_runner"]["exists"] is True

    delivery_canary_path=(
        tmp_path/"runs"/"audits"/"cortex_f2f4c_structural_canary.json"
    )
    delivery_canary_path.parent.mkdir(parents=True,exist_ok=True)
    delivery_canary_path.write_text(json.dumps({
        "status":"completed",
        "run_id":"f2f4c-test",
        "authority":"execute",
        "continuous_authority":False,
        "code_revision":{"commit":"cafe1234","dirty":False},
        "transaction_committed":False,
        "rollback_observed":True,
        "delivery_power_capability":{
            "available":False,
            "status":"observed_absent_topology",
        },
        "delivery_actuator_dependency":{
            "ready":True,
            "dependency":{
                "actuator":"burner-inserter",
                "fuel_dependency":{
                    "fuel":{"name":"coal"},
                },
            },
        },
        "measurement_before":{
            "processor_status":None,
            "processor_output":0.0,
        },
        "measurement_final":{
            "processor_status":None,
            "processor_output":0.0,
        },
        "action_result":{
            "status":"rejected",
            "refusal":{"code":"structural_postcondition_failed"},
            "measurements":{
                "candidate_after":{
                    "physical_processing_coverage":1.0,
                    "processor_exists":True,
                    "processor_status":"no_ingredients",
                    "processor_output":0.0,
                    "producers_reaching_processor":1,
                },
            },
            "postconditions":[],
        },
    })+"\n")

    f2f4c=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f2f4c["phase2_checkpoint"] == "F2-F4C"
    canary4c=f2f4c["phase2_delivery_actuator_canary"]
    assert canary4c["exists"] is True
    assert canary4c["classification"] == (
        "material_delivery_failed_with_rollback"
    )
    assert canary4c["code_commit"] == "cafe1234"
    assert canary4c["actuator"] == "burner-inserter"
    assert canary4c["actuator_fuel"] == "coal"
    assert canary4c["power_available"] is False
    assert canary4c["power_status"] == "observed_absent_topology"
    assert canary4c["rollback_observed"] is True
    assert canary4c["functional_accept"] is False
    assert canary4c["sustained_operation"] is None
    assert canary4c["sustainability_classification"] == "not_evaluated"

def test_phase_state_marks_f2f4c_functional_accept_as_unsustained_when_final_no_fuel(
    tmp_path,
) -> None:
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
        "CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY.md",
        "CORTEX_PHASE2_FUNCTIONAL_DEPENDENCY_COMPOSITION.md",
        "CORTEX_PHASE2_DELIVERY_ACTUATOR_DEPENDENCY.md",
        "CORTEX_PHASE2_DELIVERY_ACTUATOR_RUNNER.md",
    ):
        (docs/name).write_text("# checkpoint\n")

    audit=tmp_path/"runs"/"audits"
    audit.mkdir(parents=True)
    (audit/"cortex_f2f4c_structural_canary.json").write_text(json.dumps({
        "status":"completed",
        "run_id":"f2f4c-accept",
        "authority":"execute",
        "continuous_authority":False,
        "code_revision":{"commit":"20aac7f","dirty":False},
        "transaction_committed":True,
        "rollback_observed":False,
        "delivery_power_capability":{
            "available":False,
            "status":"derived_unavailable",
        },
        "delivery_actuator_dependency":{
            "ready":True,
            "dependency":{
                "actuator":"burner-inserter",
                "fuel_dependency":{"fuel":{"name":"coal"}},
            },
        },
        "measurement_before":{
            "processor_status":None,
            "processor_output":0.0,
        },
        "measurement_final":{
            "processor_status":"no_fuel",
            "processor_output":13.0,
        },
        "action_result":{
            "status":"accepted",
            "refusal":None,
            "measurements":{
                "candidate_after":{
                    "physical_processing_coverage":1.0,
                    "processor_exists":True,
                    "processor_status":"no_fuel",
                    "processor_output":13.0,
                    "producers_reaching_processor":1,
                },
            },
            "postconditions":[],
        },
    })+"\n")

    state=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    canary=state["phase2_delivery_actuator_canary"]

    assert state["phase2_checkpoint"] == "F2-F4C"
    assert canary["classification"] == "functional_accept"
    assert canary["functional_accept"] is True
    assert canary["transaction_committed"] is True
    assert canary["rollback_observed"] is False
    assert canary["processor_output"] == 13.0
    assert canary["sustained_operation"] is False
    assert canary["sustainability_classification"] == (
        "functional_accept_terminal_no_fuel"
    )

    (docs/"CORTEX_PHASE2_RUNNER_INDEPENDENCE.md").write_text(
        "# F2-G1\n"
    )
    g1=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert g1["phase2_checkpoint"] == "F2-G1"
    assert g1["phase2_runner_independence"]["exists"] is True
    assert (
        g1["phase2_delivery_actuator_canary"]["classification"]
        == "functional_accept"
    )

    (docs/"CORTEX_PHASE2_OPTIONS.md").write_text("# F2-G2\n")
    g2=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert g2["phase2_checkpoint"] == "F2-G2"
    assert g2["phase2_options"]["exists"] is True
    assert g2["phase2_runner_independence"]["exists"] is True
    assert (
        g2["phase2_delivery_actuator_canary"]["classification"]
        == "functional_accept"
    )

    (docs/"CORTEX_PHASE2_OPTION_EXECUTION_BOUNDARY.md").write_text(
        "# F2-G3\n"
    )
    g3=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert g3["phase2_checkpoint"] == "F2-G3"
    assert g3["phase2_option_execution_boundary"]["exists"] is True
    assert g3["phase2_options"]["exists"] is True
    assert (
        g3["phase2_delivery_actuator_canary"]["classification"]
        == "functional_accept"
    )

    (docs/"CORTEX_PHASE2_PERSISTENT_OPTION_AUTHORITY.md").write_text(
        "# F2-G4A\n"
    )
    g4a_doc_only=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert g4a_doc_only["phase2_checkpoint"] == "F2-G3"
    assert (
        g4a_doc_only["phase2_persistent_option_authority"]["validated"]
        is False
    )

    audits=tmp_path/"runs"/"audits"
    audits.mkdir(parents=True,exist_ok=True)
    (audits/"cortex_f2g4a_option_authority_dry_run.json").write_text(
        json.dumps({
            "status":"pass",
            "run_id":"f2g4a-test",
            "factorio_world_mutation":False,
            "continuous_authority":False,
            "live_option_execute_authorized":False,
            "code_revision":{"commit":"g4a-sha"},
        })+"\n"
    )
    g4a=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert g4a["phase2_checkpoint"] == "F2-G4A"
    authority=g4a["phase2_persistent_option_authority"]
    assert authority["exists"] is True
    assert authority["validated"] is True
    assert authority["dry_run_exists"] is True
    assert authority["dry_run_status"] == "pass"
    assert authority["world_mutation"] is False
    assert authority["continuous_authority"] is False
    assert authority["live_option_execute_authorized"] is False

    (docs/"CORTEX_PHASE2_LIVE_OPTION_CANARY.md").write_text(
        "# F2-G4B\n"
    )
    live_payload={
        "status":"completed",
        "run_id":"f2g4b-test",
        "seed":424242,
        "confirmatory_seed":False,
        "code_revision":{"commit":"g4b-sha","dirty":False},
        "automatic_retry":False,
        "option_execution_attempts":1,
        "continuous_authority":False,
        "failure":None,
        "functional_accept":True,
        "transaction_committed":True,
        "rollback_observed":False,
        "sustained_operation":False,
        "sustainability_classification":"functional_accept_terminal_no_fuel",
        "grant":{
            "grant_id":"grant-g4b",
            "plan_digest":"digest-g4b",
            "scope":{
                "experiment_id":"f2g4b-test",
                "world_lease_id":"arena:run:lease",
                "option_kind":"establish_processing_chain",
                "max_executions":1,
            },
        },
        "world_lease":{
            "status":"active",
            "lease_id":"lease",
            "scope_id":"arena:run:lease",
        },
        "world_lease_before_execute":{
            "status":"active",
            "lease_id":"lease",
            "scope_id":"arena:run:lease",
        },
        "ledger_entry_before":{
            "grant_id":"grant-g4b",
            "consumed_at":None,
            "consume_result":None,
        },
        "ledger_entry_after":{
            "grant_id":"grant-g4b",
            "consumed_at":"2026-09-25T01:00:00+00:00",
            "consume_result":"reserved_before_runtime_mutation",
        },
        "evolution_after_lease":{
            "active":"inactive",
            "enabled":"disabled",
        },
        "evolution_before_execute":{
            "active":"inactive",
            "enabled":"disabled",
        },
        "measurement_final":{
            "physical_processing_coverage":1.0,
            "processor_exists":True,
            "processor_output":13.0,
            "processor_status":"no_fuel",
            "producers_reaching_processor":1,
        },
        "option_execution_result":{
            "status":"accepted",
            "changed_world":True,
            "plan_digest":"digest-g4b",
            "grant_consumed_at":"2026-09-25T01:00:00+00:00",
            "grant_consume_result":"reserved_before_runtime_mutation",
            "tick_measurement_status":"invalid_rewound",
            "observed_ticks":None,
            "action_result":{
                "status":"accepted",
                "postconditions":[
                    {"name":"reach","hard":True,"state":"satisfied"},
                    {"name":"exists","hard":True,"state":"satisfied"},
                    {"name":"output","hard":True,"state":"satisfied"},
                ],
            },
        },
    }
    live_path=audits/"cortex_f2g4b_option_live_canary.json"
    live_path.write_text(json.dumps(live_payload)+"\n")
    live_sha=hashlib.sha256(live_path.read_bytes()).hexdigest()
    (audits/"cortex_f2g4b_temporal_audit.json").write_text(
        json.dumps({
            "live_artifact_sha256":live_sha,
            "live_run_id":"f2g4b-test",
            "classification":"functional_accept_tick_epoch_reset_explained",
            "temporal_claim":{
                "old_artifact_rewritten":False,
                "second_live_canary_executed":False,
            },
        })+"\n"
    )

    g4b=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert g4b["phase2_checkpoint"] == "F2-G4B"
    live=g4b["phase2_live_option_canary"]
    assert live["validated"] is True
    assert live["functional_accept"] is True
    assert live["transaction_committed"] is True
    assert live["option_execution_attempts"] == 1
    assert live["automatic_retry"] is False
    assert live["continuous_authority"] is False
    assert live["ledger_consumed_at"] is not None
    assert live["temporal_audit_classification"] == (
        "functional_accept_tick_epoch_reset_explained"
    )
    assert live["sustained_operation"] is False

    (docs/"CORTEX_PHASE2_BASELINE_ONLY_ENFORCEMENT.md").write_text(
        "# F2-G5\n"
    )
    g5_doc_only=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert g5_doc_only["phase2_checkpoint"] == "F2-G4B"
    assert g5_doc_only["phase_status"] == "active"
    assert g5_doc_only["phase2_exit_gate"]["validated"] is False
    assert g5_doc_only["phase2_baseline_only_enforcement"]["validated"] is False

    (audits/"cortex_f2g5_baseline_only_enforcement.json").write_text(
        json.dumps({
            "schema_version":"cortex_f2g5_baseline_only_audit_v1",
            "status":"pass",
            "code_revision":{"commit":"g5-sha","branch":"research/cortex-v1","dirty":False},
            "legacy_runner_role":"baseline",
            "fail_closed_before_environment_creation":True,
            "cortex_imports_legacy_runner":False,
            "cortex_import_offenders":[],
            "world_mutation":False,
            "factorio_rcon_used":False,
            "checks":{
                "run_curriculum_requires_execution_role":True,
                "guard_precedes_environment_creation":True,
                "role_constant_is_baseline":True,
                "cli_requires_execution_role":True,
                "corrected_baseline_labels_role":True,
                "legacy_shell_labels_role":True,
                "evolution_legacy_call_labels_role":True,
                "cortex_imports_legacy_runner":True
            }
        })+"\n"
    )
    g5=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert g5["phase"] == "F2"
    assert g5["phase_status"] == "complete"
    assert g5["phase2_checkpoint"] == "F2-G5"
    assert g5["phase2_baseline_only_enforcement"]["validated"] is True
    assert g5["phase2_exit_gate"]["validated"] is True
    assert g5["phase2_exit_gate"]["legacy_runner_baseline_only"] is True
    assert g5["resume"]["do_not_start_another_seed"] is True
    assert g5["resume"]["action"] == (
        "F2 complete; F3 ready but not started"
    )
    assert g5["phase3_checkpoint"] is None
    assert g5["phase3_executive_shadow_kernel"]["validated"] is False

    repairs=tmp_path/"runs"/"repairs.jsonl"
    repairs.write_text(
        json.dumps({
            "run_id":"legacy-observed",
            "generation":96,
            "stage":"Logistic science",
            "symptom":(
                "producer_output_unprocessed:"
                "output_buffered_not_processed"
            ),
            "action_key":"placement:place_processing_for_buffered_output",
            "executed":False,
            "targets":["u1","u2"]
        })+"\n"
    )
    repairs_sha=module._sha256(repairs)
    f3_payload={
        "schema_version":"cortex_f3a_executive_shadow_replay_v1",
        "status":"pass",
        "run_id":"f3a-shadow",
        "code_revision":{
            "commit":"f3a-sha",
            "branch":"research/cortex-v1",
            "dirty":False
        },
        "authority":"shadow",
        "world_mutation":False,
        "factorio_rcon_used":False,
        "fle_environment_created":False,
        "world_lease_acquired":False,
        "execution_grant_created":False,
        "continuous_authority":False,
        "observed_evidence":{
            "source":"runs/repairs.jsonl",
            "source_sha256":repairs_sha,
            "row_index":0,
            "run_id":"legacy-observed",
            "generation":96,
            "stage":"Logistic science",
            "symptom":(
                "producer_output_unprocessed:"
                "output_buffered_not_processed"
            ),
            "action_key":"placement:place_processing_for_buffered_output",
            "executed":False,
            "targets":["u1","u2"],
            "outcome":None
        },
        "counterfactual_expansion":{
            "observed_in_world":False,
            "candidate_count":2
        },
        "policy_replays":{
            "prefer_build":{},
            "prefer_reroute":{}
        },
        "checks":{
            "observed_symptom_present":True,
            "observed_targets_present":True,
            "candidate_count_at_least_two":True,
            "same_goal_same_candidate_set":True,
            "different_policy_different_choice":True,
            "predictions_exist_before_action":True,
            "all_candidates_hard_feasible":True,
            "build_choice_is_placement":True,
            "reroute_choice_is_rebuild":True,
            "shadow_only":True,
            "world_mutation_false":True,
            "execute_authorized_false":True
        }
    }
    f3_audit=audits/"cortex_f3a_executive_shadow_replay.json"
    f3_audit.write_text(json.dumps(f3_payload)+"\n")

    f3_artifact_only=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f3_artifact_only["phase"] == "F2"
    assert f3_artifact_only["phase_status"] == "complete"
    assert f3_artifact_only["phase3_checkpoint"] is None
    assert (
        f3_artifact_only["phase3_executive_shadow_kernel"]["validated"]
        is False
    )

    f3_doc=docs/"CORTEX_PHASE3_EXECUTIVE_SHADOW_KERNEL.md"
    f3_doc.write_text("# F3-A\n")
    f3=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f3["phase"] == "F3"
    assert f3["phase_status"] == "active"
    assert f3["phase2_checkpoint"] == "F2-G5"
    assert f3["phase2_exit_gate"]["validated"] is True
    assert f3["phase3_checkpoint"] == "F3-A"
    assert f3["phase3_executive_shadow_kernel"]["validated"] is True
    assert f3["resume"]["do_not_start_another_seed"] is True
    assert f3["resume"]["action"] == (
        "F3-A active in SHADOW; implement verification, credit assignment, "
        "and experiment ledger"
    )
    assert (
        f3["phase3_executive_shadow_kernel"]["observed_row_still_matches"]
        is True
    )

    with repairs.open("a",encoding="utf-8") as handle:
        handle.write(json.dumps({"append_only":True})+"\n")
    f3_after_append=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f3_after_append["phase"] == "F3"
    assert f3_after_append["phase3_checkpoint"] == "F3-A"
    assert (
        f3_after_append["phase3_executive_shadow_kernel"]["validated"]
        is True
    )
    assert (
        f3_after_append["phase3_executive_shadow_kernel"][
            "observed_row_still_matches"
        ]
        is True
    )
    assert (
        f3_after_append["phase3_executive_shadow_kernel"][
            "current_source_sha256"
        ]
        != f3_payload["observed_evidence"]["source_sha256"]
    )


    ledger_dir=tmp_path/"runs"/"ledger"
    ledger_dir.mkdir(parents=True,exist_ok=True)
    ledger_path=ledger_dir/"cortex_executive_episodes.sqlite3"
    connection=sqlite3.connect(ledger_path)
    connection.execute(
        """
        CREATE TABLE executive_episodes (
            episode_id TEXT PRIMARY KEY,
            payload_sha256 TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO executive_episodes(episode_id,payload_sha256) VALUES(?,?)",
        ("episode-test","digest-test"),
    )
    connection.commit()
    connection.close()

    f3b_payload={
        "schema_version":"cortex_f3b_verification_credit_replay_v1",
        "status":"pass",
        "run_id":"f3b-shadow",
        "code_revision":{
            "commit":"f3b-sha",
            "branch":"research/cortex-v1",
            "dirty":False
        },
        "authority":"shadow",
        "world_mutation":False,
        "factorio_rcon_used":False,
        "fle_environment_created":False,
        "world_lease_acquired":False,
        "execution_grant_created":False,
        "continuous_authority":False,
        "verification":{
            "selected_episode_count":1,
            "matches_recorded":1,
            "held":1,
            "missed":0,
            "unmeasured":0
        },
        "credit":{
            "eligible":1,
            "ineligible":0,
            "reward_sum":1.0,
            "mean_reward":1.0
        },
        "ledger":{
            "path":"runs/ledger/cortex_executive_episodes.sqlite3",
            "quick_check":"ok",
            "count_before":0,
            "count_after":1,
            "inserted":1,
            "already_present":0,
            "episode_count":1,
            "episode_digests":{"episode-test":"digest-test"},
            "batch_digest":"batch"
        },
        "checks":{
            "selected_episode_count_positive":True,
            "all_selected_executed":True,
            "all_verifications_match_recorded":True,
            "all_selected_credit_eligible":True,
            "no_unmeasured_selected":True,
            "ledger_quick_check_ok":True,
            "ledger_roundtrip_matches":True,
            "episode_ids_unique":True,
            "ledger_count_monotonic":True,
            "all_selected_persisted":True
        }
    }
    f3b_audit=audits/"cortex_f3b_verification_credit_replay.json"
    f3b_audit.write_text(json.dumps(f3b_payload)+"\n")

    f3b_artifact_only=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f3b_artifact_only["phase"] == "F3"
    assert f3b_artifact_only["phase3_checkpoint"] == "F3-A"
    assert (
        f3b_artifact_only["phase3_verification_credit_ledger"]["validated"]
        is False
    )

    f3b_doc=docs/"CORTEX_PHASE3_VERIFICATION_CREDIT_LEDGER.md"
    f3b_doc.write_text("# F3-B\n")
    f3b=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f3b["phase"] == "F3"
    assert f3b["phase_status"] == "active"
    assert f3b["phase3_checkpoint"] == "F3-B"
    credit=f3b["phase3_verification_credit_ledger"]
    assert credit["validated"] is True
    assert credit["ledger_persisted_digests_match"] is True
    assert credit["ledger_live_quick_check"] == "ok"
    assert credit["ledger_live_count"] == 1
    assert f3b["resume"]["do_not_start_another_seed"] is True
    assert f3b["resume"]["action"] == (
        "F3-B active in SHADOW; implement paired shadow comparison "
        "against the legacy runner"
    )

    assert f3b["phase3_exit_gate"]["validated"] is False

    paired_rows=[
        {
            "run_id":"paired-r1",
            "generation":97,
            "stage":"Logistic science",
            "symptom":(
                "producer_output_unprocessed:"
                "output_buffered_not_processed"
            ),
            "action_key":"placement:place_processing_for_buffered_output",
            "choice_basis":"fixed_rule_no_history",
            "executed":False,
            "targets":["p1","p2"],
            "outcome":{
                "before":None,
                "after":None,
                "prediction":{
                    "metric":"producers_reaching_processor",
                    "direction":"increase"
                },
                "reward":None,
                "verdict":"unmeasured"
            }
        },
        {
            "run_id":"paired-r2",
            "generation":98,
            "stage":"Logistic science",
            "symptom":(
                "producer_chain_reaches_no_sink:"
                "chain_reaches_no_sink"
            ),
            "action_key":"rebuild:reroute_producer_logistics",
            "choice_basis":"fixed_rule_no_history",
            "executed":False,
            "targets":["p3"],
            "outcome":{
                "before":None,
                "after":None,
                "prediction":{
                    "metric":"producers_reaching_processor",
                    "direction":"increase"
                },
                "reward":None,
                "verdict":"unmeasured"
            }
        },
    ]
    with repairs.open("a",encoding="utf-8") as handle:
        for row in paired_rows:
            handle.write(json.dumps(row)+"\n")
    f3c_pairs=[]
    for row_index,row in zip((2,3),paired_rows,strict=True):
        f3c_pairs.append({
            "pair_id":f"pair-{row_index}",
            "observed":{
                "row_index":row_index,
                "run_id":row["run_id"],
                "generation":row["generation"],
                "stage":row["stage"],
                "symptom":row["symptom"],
                "action_key":row["action_key"],
                "choice_basis":row["choice_basis"],
                "executed":row["executed"],
                "targets":row["targets"],
                "outcome":row["outcome"],
                "identity_sha256":"fixture"
            },
            "candidate_action_keys":["a","b"],
            "candidate_count":2,
            "same_candidate_set_between_policies":True,
            "legacy_action_in_candidate_set":True,
            "legacy_policy_choice":row["action_key"],
            "rebuild_policy_choice":(
                "rebuild:reroute_producer_logistics"
            ),
            "legacy_policy_agrees_with_runner":True,
            "rebuild_policy_agrees_with_runner":row_index==3,
            "policies_diverge":row_index==2,
            "world_mutation":False,
            "execute_authorized":False
        })
    f3c_payload={
        "schema_version":"cortex_f3c_paired_shadow_comparison_v1",
        "status":"pass",
        "run_id":"f3c-shadow",
        "code_revision":{
            "commit":"f3c-sha",
            "branch":"research/cortex-v1",
            "dirty":False
        },
        "authority":"shadow",
        "world_mutation":False,
        "factorio_rcon_used":False,
        "fle_environment_created":False,
        "world_lease_acquired":False,
        "execution_grant_created":False,
        "continuous_authority":False,
        "comparison":{
            "paired_episode_count":2,
            "fixed_rule_basis_count":2,
            "canonical_fixed_rule_agreement":2,
            "canonical_fixed_rule_agreement_rate":1.0,
            "multiple_candidate_pairs":2,
            "candidate_set_policy_invariant_pairs":2,
            "legacy_action_coverage":2,
            "legacy_policy_agreement":2,
            "legacy_policy_agreement_rate":1.0,
            "rebuild_policy_agreement":1,
            "rebuild_policy_agreement_rate":0.5,
            "policy_divergence_pairs":1,
            "policy_divergence_rate":0.5,
            "observed_unexecuted_pairs":2,
            "observed_reward_count":0
        },
        "pairs":f3c_pairs,
        "checks":{
            "paired_episode_count_positive":True,
            "all_pairs_fixed_rule_no_history":True,
            "canonical_fixed_rule_matches_every_runner_choice":True,
            "all_pairs_have_multiple_candidates":True,
            "candidate_sets_policy_invariant":True,
            "legacy_action_covered_in_every_pair":True,
            "fixed_legacy_policy_matches_every_runner_choice":True,
            "alternative_policy_diverges_on_at_least_one_pair":True,
            "all_observed_structural_actions_unexecuted":True,
            "no_observed_reward_used_for_structural_comparison":True,
            "shadow_only":True,
            "world_mutation_false":True,
            "execute_authorized_false":True
        }
    }
    f3c_audit=audits/"cortex_f3c_paired_shadow_comparison.json"
    f3c_audit.write_text(json.dumps(f3c_payload)+"\n")

    f3c_artifact_only=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f3c_artifact_only["phase"] == "F3"
    assert f3c_artifact_only["phase_status"] == "active"
    assert f3c_artifact_only["phase3_checkpoint"] == "F3-B"
    assert (
        f3c_artifact_only["phase3_paired_shadow_comparison"]["validated"]
        is False
    )
    assert f3c_artifact_only["phase3_exit_gate"]["validated"] is False

    f3c_doc=docs/"CORTEX_PHASE3_PAIRED_SHADOW_COMPARISON.md"
    f3c_doc.write_text("# F3-C\n")
    f3c=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f3c["phase"] == "F3"
    assert f3c["phase_status"] == "complete"
    assert f3c["phase3_checkpoint"] == "F3-C"
    assert f3c["phase3_exit_gate"]["validated"] is True
    assert f3c["phase3_paired_shadow_comparison"]["validated"] is True
    assert (
        f3c["phase3_paired_shadow_comparison"][
            "pairs_still_match_observed_rows"
        ]
        is True
    )
    assert f3c["resume"]["do_not_start_another_seed"] is True
    assert f3c["resume"]["action"] == (
        "F3 complete; F4 ready but not started"
    )

    assert f3c["phase4_checkpoint"] is None
    assert f3c["phase4_memory_substrate"]["validated"] is False

    memory_path=ledger_dir/"cortex_cognitive_memory.sqlite3"
    connection=sqlite3.connect(memory_path)
    connection.execute(
        """
        CREATE TABLE memory_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO memory_meta(key,value) VALUES(?,?)",
        ("schema_version","cortex_cognitive_memory_v1"),
    )
    connection.execute(
        """
        CREATE TABLE memory_items (
            memory_id TEXT PRIMARY KEY,
            item_digest TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE memory_occurrences (
            occurrence_id TEXT PRIMARY KEY,
            memory_id TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            qualified INTEGER NOT NULL,
            contradiction INTEGER NOT NULL,
            reward REAL,
            batch_id TEXT NOT NULL
        )
        """
    )
    batch_id="cortex-f4a-fixture"
    for index,kind in enumerate(
        ("episodic","semantic","procedural","counterexample"),
        start=1,
    ):
        memory_id=f"memory-{kind}"
        connection.execute(
            "INSERT INTO memory_items(memory_id,item_digest) VALUES(?,?)",
            (memory_id,f"digest-{kind}"),
        )
        connection.execute(
            """
            INSERT INTO memory_occurrences(
                occurrence_id,
                memory_id,
                payload_sha256,
                qualified,
                contradiction,
                reward,
                batch_id
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                f"occurrence-{index}",
                memory_id,
                f"payload-{kind}",
                1,
                0,
                1.0 if kind=="procedural" else None,
                batch_id,
            ),
        )
    connection.commit()
    connection.close()
    (
        memory_quick,
        memory_schema,
        memory_batch_count,
        memory_manifest_sha,
    )=module._memory_batch_manifest(memory_path,batch_id)
    assert memory_quick=="ok"
    assert memory_schema=="cortex_cognitive_memory_v1"
    assert memory_batch_count==4
    assert isinstance(memory_manifest_sha,str)

    f4a_payload={
        "schema_version":"cortex_f4a_memory_substrate_migration_v1",
        "status":"pass",
        "run_id":batch_id,
        "code_revision":{
            "commit":"f4a-sha",
            "branch":"research/cortex-v1",
            "dirty":False
        },
        "authority":"shadow",
        "world_mutation":False,
        "factorio_rcon_used":False,
        "fle_environment_created":False,
        "world_lease_acquired":False,
        "execution_grant_created":False,
        "continuous_authority":False,
        "working_memory":{
            "capacity":3,
            "size":3,
            "keys":["belief","candidate","prediction"],
            "evicted_oldest":True,
            "persistent":False
        },
        "store":{
            "path":"runs/ledger/cortex_cognitive_memory.sqlite3",
            "quick_check":"ok",
            "snapshot":{
                "episodic":{
                    "items":1,
                    "occurrences":1,
                    "qualified_occurrences":1
                },
                "semantic":{
                    "items":1,
                    "occurrences":1,
                    "qualified_occurrences":1
                },
                "procedural":{
                    "items":1,
                    "occurrences":1,
                    "qualified_occurrences":1
                },
                "counterexample":{
                    "items":1,
                    "occurrences":1,
                    "qualified_occurrences":1
                }
            },
            "batch_manifest":{
                "batch_id":batch_id,
                "occurrence_count":4,
                "manifest_sha256":memory_manifest_sha
            }
        },
        "checks":{
            "executive_ledger_quick_check_ok":True,
            "memory_quick_check_ok":True,
            "working_memory_bounded":True,
            "episodic_occurrences_cover_executive_episodes":True,
            "semantic_verified_rows_imported":True,
            "semantic_qualified_support_preserved":True,
            "semantic_deduplicated":True,
            "procedural_credit_only":True,
            "procedural_confidence_empirical":True,
            "counterexamples_first_class":True,
            "batch_manifest_complete":True,
            "no_live_authority":True
        }
    }
    f4a_audit=audits/"cortex_f4a_memory_substrate_migration.json"
    f4a_audit.write_text(json.dumps(f4a_payload)+"\n")

    f4a_artifact_only=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f4a_artifact_only["phase"]=="F3"
    assert f4a_artifact_only["phase_status"]=="complete"
    assert f4a_artifact_only["phase4_checkpoint"] is None
    assert (
        f4a_artifact_only["phase4_memory_substrate"]["validated"]
        is False
    )

    f4a_doc=docs/"CORTEX_PHASE4_MEMORY_SUBSTRATE.md"
    f4a_doc.write_text("# F4-A\n")
    f4a=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f4a["phase"]=="F4"
    assert f4a["phase_status"]=="active"
    assert f4a["phase3_checkpoint"]=="F3-C"
    assert f4a["phase3_exit_gate"]["validated"] is True
    assert f4a["phase4_checkpoint"]=="F4-A"
    memory=f4a["phase4_memory_substrate"]
    assert memory["validated"] is True
    assert memory["live_quick_check"]=="ok"
    assert memory["live_schema"]=="cortex_cognitive_memory_v1"
    assert memory["batch_manifest_matches"] is True
    assert memory["live_batch_occurrence_count"]==4
    assert f4a["resume"]["do_not_start_another_seed"] is True
    assert f4a["resume"]["action"] == (
        "F4-A active in SHADOW; implement hybrid retrieval, "
        "consolidation, and decay"
    )

    f4a_sha=module._sha256(f4a_audit)
    database_snapshot={
        "schema_version":"cortex_cognitive_memory_v1",
        "quick_check":"ok",
        "item_count":4,
        "occurrence_count":4,
        "manifest_sha256":"a"*64
    }
    retrieval_keys=(
        "semantic-smelting-output",
        "semantic-smelting-placement-error",
        "cross-kind-fuel-procedure",
        "counterexample-electric-route-buffer",
    )
    f4b_payload={
        "schema_version":"cortex_f4b_memory_retrieval_v1",
        "status":"pass",
        "run_id":"f4b-shadow",
        "code_revision":{
            "commit":"f4b-sha",
            "branch":"research/cortex-v1",
            "dirty":False
        },
        "authority":"shadow",
        "world_mutation":False,
        "factorio_rcon_used":False,
        "fle_environment_created":False,
        "world_lease_acquired":False,
        "execution_grant_created":False,
        "continuous_authority":False,
        "source":{
            "memory_path":"runs/ledger/cortex_cognitive_memory.sqlite3",
            "database_before":database_snapshot,
            "database_after":database_snapshot,
            "f4a_artifact_path":"runs/audits/cortex_f4a_memory_substrate_migration.json",
            "f4a_artifact_sha256":f4a_sha
        },
        "retrievals":{
            key:{
                "query":{"query_id":key},
                "policy_version":"cortex_hybrid_retrieval_v1",
                "records_considered":4,
                "records_compatible":1,
                "results":[{"memory_id":"memory-"+str(index)}]
            }
            for index,key in enumerate(retrieval_keys)
        },
        "consolidation":{
            "policy_version":"cortex_memory_consolidation_v1",
            "repeated_semantic_items":1,
            "semantic_duplicate_support":3,
            "repeated_counterexample_items":1,
            "procedural_confidence_items":1,
            "snapshot_sha256":"b"*64
        },
        "decay_probe":{
            "policy_version":"cortex_non_destructive_decay_v1",
            "destructive_deletion":False,
            "low_support":{"weight":0.2},
            "high_support":{"weight":0.9}
        },
        "checks":{
            "memory_database_quick_check_ok":True,
            "memory_database_unchanged_by_replay":True,
            "memory_database_has_multiple_kinds":True,
            "semantic_stage_scope_enforced":True,
            "lexical_component_changes_top_memory_same_stage":True,
            "procedural_memory_ranked_first_by_symptom":True,
            "counterexample_scope_enforced":True,
            "semantic_duplicate_support_consolidated":True,
            "counterexample_repetition_visible":True,
            "procedural_confidence_visible":True,
            "decay_is_non_destructive_and_support_protective":True,
            "f4a_artifact_present":True,
            "no_live_authority":True
        }
    }
    f4b_audit=audits/"cortex_f4b_memory_retrieval.json"
    f4b_audit.write_text(json.dumps(f4b_payload)+"\n")

    f4b_artifact_only=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f4b_artifact_only["phase"]=="F4"
    assert f4b_artifact_only["phase_status"]=="active"
    assert f4b_artifact_only["phase4_checkpoint"]=="F4-A"
    assert f4b_artifact_only["phase4_memory_retrieval"]["validated"] is False

    f4b_doc=docs/"CORTEX_PHASE4_MEMORY_RETRIEVAL.md"
    f4b_doc.write_text("# F4-B\n")
    f4b=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f4b["phase"]=="F4"
    assert f4b["phase_status"]=="active"
    assert f4b["phase4_checkpoint"]=="F4-B"
    retrieval=f4b["phase4_memory_retrieval"]
    assert retrieval["validated"] is True
    assert retrieval["database_read_only_replay"] is True
    assert retrieval["f4a_artifact_hash_matches"] is True
    assert f4b["phase4_memory_substrate"]["validated"] is True
    assert f4b["resume"]["do_not_start_another_seed"] is True
    assert f4b["resume"]["action"] == (
        "F4-B complete in SHADOW; F4-C requires a frozen, diverse "
        "non-confirmatory held-out memory-ablation transfer protocol"
    )
    assert f4b["phase4_next_checkpoint"]=="F4-C"
    assert f4b["phase4_exit_gate"] == {
        "memory_substrate":True,
        "hybrid_retrieval_consolidation_decay":True,
        "causal_memory_ablation_transfer":False,
        "validated":False,
    }
    assert f4b["phase4_blocker"]["status"]=="blocked"
    assert (
        f4b["phase4_blocker"]["code"]
        =="causal_transfer_protocol_not_frozen"
    )
    assert "Confirmatory seeds remain frozen" in (
        f4b["phase4_blocker"]["detail"] or ""
    )

    configs=tmp_path/"configs"
    configs.mkdir(exist_ok=True)
    f4c_doc=docs/"CORTEX_PHASE4_CAUSAL_ABLATION_PROTOCOL.md"
    f4c_doc.write_text("# F4-C protocol\n")
    f4c_manifest_payload={
        "schema_version":"cortex_f4c_causal_ablation_protocol_v1",
        "protocol_id":"cortex-f4c-memory-ablation-transfer-v1",
        "status":"frozen",
        "authority":"shadow",
        "continuous_authority":False,
        "source_memory":{"manifest_sha256":"a"*64},
        "seed_partitions":{
            "confirmatory_reserved":list(range(20261101,20261111))
        },
    }
    f4c_manifest=configs/"cortex_f4c_causal_ablation_v1.json"
    f4c_manifest.write_text(json.dumps(f4c_manifest_payload,sort_keys=True)+"\n")
    f4c_manifest_file_sha=module._sha256(f4c_manifest)
    f4b_sha=module._sha256(f4b_audit)
    f4c_audit_payload={
        "schema_version":"cortex_f4c_protocol_freeze_v1",
        "status":"pass",
        "authority":"shadow",
        "world_mutation":False,
        "factorio_rcon_used":False,
        "fle_environment_created":False,
        "world_lease_acquired":False,
        "execution_grant_created":False,
        "continuous_authority":False,
        "code_revision":{
            "commit":"f4c-protocol-sha",
            "branch":"research/cortex-v1",
            "dirty":False,
        },
        "protocol":{
            "protocol_id":"cortex-f4c-memory-ablation-transfer-v1",
            "file_sha256":f4c_manifest_file_sha,
            "manifest_sha256":"b"*64,
            "evaluation_pair_count":20,
            "pilot_pair_count":8,
            "task_family_count":4,
            "memory_on_first":10,
            "memory_ablated_first":10,
            "confirmatory_reserved":list(range(20261101,20261111)),
        },
        "source":{
            "f4b_artifact_sha256":f4b_sha,
            "memory_manifest_sha256":"a"*64,
        },
        "checks":{
            "protocol_matches_frozen_v1_builder":True,
            "seed_partitions_disjoint":True,
            "counterbalancing_valid":True,
            "missing_is_never_zero":True,
        },
    }
    f4c_audit=audits/"cortex_f4c_protocol_freeze.json"
    f4c_audit.write_text(json.dumps(f4c_audit_payload)+"\n")

    f4c_ready=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f4c_ready["phase4_checkpoint"]=="F4-B"
    assert f4c_ready["phase4_next_checkpoint"]=="F4-C"
    assert f4c_ready["phase4_causal_protocol"]["validated"] is True
    assert f4c_ready["phase4_causal_protocol"]["eligible"] is True
    assert f4c_ready["phase4_causal_protocol"]["execution_ready"] is False
    assert (
        f4c_ready["phase4_blocker"]["code"]
        =="causal_transfer_evaluation_harness_not_validated"
    )
    assert f4c_ready["resume"]["do_not_start_another_seed"] is True
    assert "paired evaluation harness" in f4c_ready["resume"]["action"]

    harness_doc=docs/"CORTEX_PHASE4_CAUSAL_HARNESS.md"
    harness_doc.write_text("# F4-C harness synthetic preflight\n")
    harness_module=(
        tmp_path/"src"/"factorio_ai_lab"/"cortex"/"causal_harness.py"
    )
    harness_module.parent.mkdir(parents=True,exist_ok=True)
    harness_module.write_text("SCHEMA_VERSION='fixture'\n")
    harness_validator=tmp_path/"scripts"/"validate_cortex_f4c_harness.py"
    harness_validator.parent.mkdir(parents=True,exist_ok=True)
    harness_validator.write_text("# fixture validator\n")
    harness_tests=tmp_path/"tests"/"test_cortex_f4c_causal_harness.py"
    harness_tests.parent.mkdir(parents=True,exist_ok=True)
    harness_tests.write_text("# fixture tests\n")
    harness_payload={
        "schema_version":"cortex_f4c_harness_validation_v1",
        "status":"pass",
        "mode":"synthetic_preflight",
        "authority":"shadow",
        "world_mutation":False,
        "factorio_rcon_used":False,
        "fle_environment_created":False,
        "world_lease_acquired":False,
        "execution_grant_created":False,
        "continuous_authority":False,
        "experimental_seed_executed":False,
        "code_revision":{
            "commit":"f4c-harness-sha",
            "branch":"research/cortex-v1",
            "dirty":False,
        },
        "protocol":{
            "protocol_id":"cortex-f4c-memory-ablation-transfer-v1",
            "manifest_file_sha256":f4c_manifest_file_sha,
            "manifest_sha256":"b"*64,
        },
        "source":{
            "database_before":database_snapshot,
            "database_after":database_snapshot,
            "harness_module_sha256":module._sha256(harness_module),
            "validator_script_sha256":module._sha256(harness_validator),
            "tests_sha256":module._sha256(harness_tests),
            "document_sha256":module._sha256(harness_doc),
        },
        "checks":{
            "protocol_matches_frozen_v1":True,
            "source_memory_quick_check_ok":True,
            "source_memory_matches_frozen_manifest":True,
            "source_memory_unchanged_by_preflight":True,
            "four_task_families_exercised":True,
            "all_synthetic_pairs_valid":True,
            "checkpoint_restore_exact":True,
            "retrieval_only_ablation_enforced":True,
            "matched_budget_and_surface_enforced":True,
            "quarantined_writes_do_not_touch_source":True,
            "outcome_J_and_delta_recomputable":True,
            "missing_primary_component_fails_closed":True,
            "budget_overrun_fails_closed":True,
            "no_live_authority":True,
        },
    }
    harness_audit=audits/"cortex_f4c_harness_validation.json"
    harness_audit.write_text(json.dumps(harness_payload)+"\n")
    f4c_harness_ready=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert (
        f4c_harness_ready["phase4_causal_protocol"][
            "harness_preflight_validated"
        ]
        is True
    )
    assert f4c_harness_ready["phase4_causal_protocol"]["execution_ready"] is False
    assert (
        f4c_harness_ready["phase4_causal_harness"]["preflight_validated"]
        is True
    )
    assert (
        f4c_harness_ready["phase4_causal_harness"][
            "real_task_adapters_validated"
        ]
        is False
    )
    assert (
        f4c_harness_ready["phase4_blocker"]["code"]
        =="causal_transfer_real_task_adapters_not_validated"
    )
    assert f4c_harness_ready["resume"]["do_not_start_another_seed"] is True
    assert "do not launch any pilot seed yet" in (
        f4c_harness_ready["resume"]["action"]
    )

    f4b_payload["source"]["f4a_artifact_sha256"]="wrong"
    f4b_audit.write_text(json.dumps(f4b_payload)+"\n")
    f4b_wrong_source=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f4b_wrong_source["phase4_checkpoint"]=="F4-A"
    assert (
        f4b_wrong_source["phase4_memory_retrieval"]["validated"]
        is False
    )
    f4b_audit.write_text(
        json.dumps({
            **f4b_payload,
            "source":{
                **f4b_payload["source"],
                "f4a_artifact_sha256":f4a_sha
            }
        })+"\n"
    )

    connection=sqlite3.connect(memory_path)
    connection.execute(
        "UPDATE memory_items SET item_digest=? WHERE memory_id=?",
        ("tampered","memory-semantic"),
    )
    connection.commit()
    connection.close()
    f4a_tampered=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f4a_tampered["phase"]=="F3"
    assert f4a_tampered["phase_status"]=="complete"
    assert f4a_tampered["phase4_checkpoint"] is None
    assert (
        f4a_tampered["phase4_memory_substrate"]["validated"]
        is False
    )

    connection=sqlite3.connect(ledger_path)
    connection.execute(
        "UPDATE executive_episodes SET payload_sha256=? WHERE episode_id=?",
        ("tampered","episode-test"),
    )
    connection.commit()
    connection.close()
    f3b_tampered=module.build_phase_state(
        state_root=tmp_path,
        protocol_path=protocol,
    )
    assert f3b_tampered["phase3_checkpoint"] == "F3-A"
    assert (
        f3b_tampered["phase3_verification_credit_ledger"]["validated"]
        is False
    )
