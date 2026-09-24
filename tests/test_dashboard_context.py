from __future__ import annotations

import json
from pathlib import Path

from factorio_ai_lab.dashboard.context import discover_experiment_context


def _seed(
    root: Path,
    *,
    seed: int,
    status: str,
    started: str,
    finished: str | None = None,
) -> Path:
    path=root/"baseline_runs"/"p1"/"exploratory"/str(seed)
    (path/"runs").mkdir(parents=True)
    manifest={
        "protocol":"p1",
        "mode":"exploratory",
        "seed":seed,
        "status":status,
        "started_at":started,
        "finished_at":finished,
        "release":{"commit":"abc","dirty":False},
    }
    (path/"manifest.json").write_text(json.dumps(manifest)+"\n")
    (path/"runs"/"research_state.json").write_text(json.dumps({
        "run_id":f"run-{seed}",
        "metrics":{"logistic_science_output":0.0},
    })+"\n")
    return path


def test_global_scope_is_explicit(tmp_path) -> None:
    ctx=discover_experiment_context(tmp_path,scope="global")
    assert ctx["kind"] == "global"
    assert ctx["runs_dir"] == str(tmp_path/"runs")


def test_running_seed_wins_over_completed_seed(tmp_path) -> None:
    _seed(
        tmp_path,
        seed=1,
        status="completed",
        started="2026-01-01T00:00:00+00:00",
        finished="2026-01-01T01:00:00+00:00",
    )
    _seed(
        tmp_path,
        seed=2,
        status="running",
        started="2026-01-01T02:00:00+00:00",
    )
    ctx=discover_experiment_context(
        tmp_path,
        scope="baseline:p1:exploratory:auto",
    )
    assert ctx["seed"] == 2
    assert ctx["status"] == "running"
    assert ctx["scope"] == "isolated_seed"


def test_latest_completed_seed_is_selected_when_idle(tmp_path) -> None:
    first=_seed(
        tmp_path,
        seed=1,
        status="completed",
        started="2026-01-01T00:00:00+00:00",
        finished="2026-01-01T01:00:00+00:00",
    )
    second=_seed(
        tmp_path,
        seed=2,
        status="completed",
        started="2026-01-02T00:00:00+00:00",
        finished="2026-01-02T01:00:00+00:00",
    )
    (second/"result.json").write_text(json.dumps({
        "completed_stage_count":14,
        "bottleneck":"Logistic science",
        "challenger":{
            "run_id":"run-2",
            "fitness":{
                "failed_stages":["Logistic science"],
                "autonomy_score":0.5,
                "closed_loop_autonomy":False,
                "physical_processing_coverage":1/3,
                "manual_logistics_calls":53,
            },
        },
    })+"\n")

    ctx=discover_experiment_context(
        tmp_path,
        scope="baseline:p1:exploratory:auto",
    )

    assert ctx["seed"] == 2
    assert ctx["runs_dir"] == str(second/"runs")
    assert ctx["result_summary"]["bottleneck"] == "Logistic science"
    assert ctx["result_summary"]["logistic_science_output"] == 0.0
    assert "result" not in ctx
    assert first.exists()


def test_series_progress_uses_frozen_protocol_seed_count(tmp_path) -> None:
    protocol = tmp_path / "protocol.json"
    protocol.write_text(
        json.dumps(
            {
                "schema_version": "p1",
                "exploratory_seeds": [1, 2, 3, 4, 5],
            }
        )
        + "\n"
    )
    for seed, finished in ((1, "2026-01-01T01:00:00+00:00"), (2, "2026-01-02T01:00:00+00:00")):
        path = _seed(
            tmp_path,
            seed=seed,
            status="completed",
            started=finished,
            finished=finished,
        )
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["protocol_path"] = str(protocol)
        (path / "manifest.json").write_text(json.dumps(manifest) + "\n")
        (path / "result.json").write_text("{}\n")

    ctx = discover_experiment_context(
        tmp_path,
        scope="baseline:p1:exploratory:auto",
    )

    assert ctx["series_progress"] == {
        "completed": 2,
        "configured": 5,
        "running": 0,
        "pending": 3,
    }


def test_missing_baseline_does_not_fall_back_silently(tmp_path) -> None:
    ctx=discover_experiment_context(
        tmp_path,
        scope="baseline:p1:exploratory:auto",
    )
    assert ctx["kind"] == "baseline_seed"
    assert ctx["status"] == "missing"
    assert "error" in ctx

def test_baseline_context_exposes_mechanical_cortex_checkpoint(tmp_path) -> None:
    path=_seed(
        tmp_path,
        seed=1,
        status="completed",
        started="2026-01-01T00:00:00+00:00",
        finished="2026-01-01T01:00:00+00:00",
    )
    (path/"result.json").write_text("{}\n")
    runs=tmp_path/"runs"
    runs.mkdir()
    (runs/"cortex_phase_state.json").write_text(json.dumps({
        "phase":"F2",
        "phase_status":"active",
        "phase2_checkpoint":"F2-D",
    })+"\n")

    ctx=discover_experiment_context(
        tmp_path,
        scope="baseline:p1:exploratory:auto",
    )

    assert ctx["cortex_phase"]["phase"] == "F2"
    assert ctx["cortex_phase"]["phase2_checkpoint"] == "F2-D"
