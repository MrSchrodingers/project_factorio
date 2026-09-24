from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module():
    path=ROOT/"scripts"/"analyze_corrected_baseline.py"
    spec=importlib.util.spec_from_file_location("analyze_corrected_baseline",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(tmp_path: Path, *, seed: int=11, commit: str="abc", completed: bool=True) -> Path:
    path=tmp_path/str(seed)
    path.mkdir()
    manifest={
        "seed":seed,
        "status":"completed" if completed else "running",
        "returncode":0 if completed else None,
        "release":{"commit":commit},
    }
    (path/"manifest.json").write_text(json.dumps(manifest)+"\n")
    result={
        "code_revision":{"commit":commit,"dirty":False},
        "completed_stage_count":3,
        "bottleneck":"d",
        "challenger":{
            "generation":1,
            "run_id":"run",
            "fitness":{
                "measurement_protocol":"cell_attributed_v3",
                "completed_stages":["a","b","c"],
                "failed_stages":["d"],
                "capabilities":["x"],
                "closed_loop_autonomy":False,
                "halt_cause":"fuel_starvation",
                "autonomy_score":0.5,
                "manual_logistics_calls":4,
                "physical_processing_coverage":0.75,
                "fuel_starved_entities":1,
                "power_starved_entities":0,
                "external_dependencies":0,
                "endogenous_rate_per_s":1.0,
                "intervention_rate_per_s":0.2,
                "productive_runtime_s":10.0,
                "route_cost":5.0,
                "route_turns":1,
            },
        },
    }
    (path/"result.json").write_text(json.dumps(result)+"\n")
    runs=path/"runs"
    runs.mkdir()
    (runs/"research_state.json").write_text(json.dumps({
        "metrics":{
            "logistic_science_output":0.0,
            "logistic_science_rate_per_s":0.0,
        },
    })+"\n")
    return path


def test_validate_seed_accepts_clean_attributable_result(tmp_path) -> None:
    module=_module()
    row=module.validate_seed(_seed(tmp_path),expected_commit="abc")
    assert row["valid"] is True
    assert row["metrics"]["autonomy_score"] == 0.5
    assert row["completed_stage_count"] == 3
    assert row["bottleneck"] == "d"
    assert row["logistic_science_output"] == 0.0


def test_commit_mismatch_invalidates_seed(tmp_path) -> None:
    module=_module()
    row=module.validate_seed(_seed(tmp_path),expected_commit="different")
    assert row["valid"] is False
    assert any("expected baseline commit" in error for error in row["errors"])


def test_running_seed_is_not_valid_result(tmp_path) -> None:
    module=_module()
    row=module.validate_seed(_seed(tmp_path,completed=False))
    assert row["valid"] is False
    assert any("manifest status" in error for error in row["errors"])


def test_summary_keeps_failed_or_invalid_records_visible(tmp_path) -> None:
    module=_module()
    good=module.validate_seed(_seed(tmp_path,seed=11))
    bad=dict(good)
    bad["seed"]=12
    bad["valid"]=False
    bad["errors"]=["infra"]
    summary=module.summarize([good,bad])
    assert summary["seed_count"] == 2
    assert summary["valid_seed_count"] == 1
    assert summary["invalid_seed_count"] == 1
    assert summary["metric_summary"]["autonomy_score"]["median"] == 0.5

def test_summary_reports_dispersion_bottlenecks_and_stage_rates(tmp_path) -> None:
    module=_module()
    first=module.validate_seed(_seed(tmp_path,seed=11))
    second=module.validate_seed(_seed(tmp_path,seed=12))
    second["metrics"]=dict(second["metrics"])
    second["metrics"]["autonomy_score"]=0.75
    second["logistic_science_output"]=1.0

    summary=module.summarize([first,second])
    stats=summary["metric_summary"]["autonomy_score"]

    assert stats["n"] == 2
    assert stats["mean"] == 0.625
    assert stats["median"] == 0.625
    assert stats["sample_stdev"] > 0
    assert stats["q1"] <= stats["median"] <= stats["q3"]
    assert stats["iqr"] == stats["q3"] - stats["q1"]
    assert summary["bottlenecks"] == {"d": 2}
    assert summary["failed_stage_counts"] == {"d": 2}
    assert summary["green_science_successes"] == 1
    assert summary["stage_completion_rates"]["a"] == 1.0


def test_markdown_labels_five_seed_summary_exploratory_not_confirmatory(tmp_path) -> None:
    module=_module()
    records=[
        module.validate_seed(_seed(tmp_path,seed=seed))
        for seed in range(11,16)
    ]
    summary=module.summarize(records)
    rendered=module.render_markdown(summary)

    assert "Five exploratory seeds are complete" in rendered
    assert "not a confirmatory performance claim" in rendered
    assert "confirmatory seeds remain unspent" in rendered
