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
