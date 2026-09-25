from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/run_cortex_f3c_shadow_comparison.py"


def _module():
    spec = importlib.util.spec_from_file_location("f3c_replay", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(
    *,
    run_id: str,
    symptom: str,
    action_key: str,
) -> dict:
    return {
        "run_id": run_id,
        "generation": 1,
        "stage": "Logistic science",
        "symptom": symptom,
        "action_key": action_key,
        "choice_basis": "fixed_rule_no_history",
        "executed": False,
        "targets": ["u1", "u2"],
        "outcome": {
            "before": None,
            "after": None,
            "prediction": {
                "metric": "producers_reaching_processor",
                "direction": "increase",
            },
            "reward": None,
            "verdict": "unmeasured",
        },
    }


def test_f3c_paired_shadow_comparison_exposes_agreement_and_divergence(
    tmp_path: Path,
) -> None:
    module = _module()
    repairs = tmp_path / "runs" / "repairs.jsonl"
    repairs.parent.mkdir(parents=True)
    rows = [
        _row(
            run_id="r1",
            symptom=module.SYMPTOM_OUTPUT_UNPROCESSED,
            action_key="placement:place_processing_for_buffered_output",
        ),
        _row(
            run_id="r2",
            symptom=module.SYMPTOM_CHAIN_NO_SINK,
            action_key="rebuild:reroute_producer_logistics",
        ),
    ]
    repairs.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    payload = module.build_replay(
        root=tmp_path,
        repairs_path=repairs,
        revision={
            "commit": "sha",
            "branch": "research/cortex-v1",
            "dirty": False,
        },
    )

    assert payload["status"] == "pass"
    comparison = payload["comparison"]
    assert comparison["paired_episode_count"] == 2
    assert comparison["fixed_rule_basis_count"] == 2
    assert comparison["canonical_fixed_rule_agreement"] == 2
    assert comparison["canonical_fixed_rule_agreement_rate"] == 1.0
    assert comparison["multiple_candidate_pairs"] == 2
    assert comparison["legacy_action_coverage"] == 2
    assert comparison["legacy_policy_agreement"] == 2
    assert comparison["legacy_policy_agreement_rate"] == 1.0
    assert comparison["rebuild_policy_agreement"] == 1
    assert comparison["policy_divergence_pairs"] == 1
    assert comparison["policy_divergence_rate"] == 0.5
    assert comparison["observed_unexecuted_pairs"] == 2
    assert comparison["observed_reward_count"] == 0
    assert all(
        len(pair["candidate_action_keys"]) >= 2
        for pair in payload["pairs"]
    )
    assert all(pair["world_mutation"] is False for pair in payload["pairs"])
    assert all(
        pair["execute_authorized"] is False for pair in payload["pairs"]
    )


def test_f3c_replay_refuses_dirty_revision(tmp_path: Path) -> None:
    module = _module()
    repairs = tmp_path / "runs" / "repairs.jsonl"
    repairs.parent.mkdir(parents=True)
    repairs.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="clean source tree"):
        module.build_replay(
            root=tmp_path,
            repairs_path=repairs,
            revision={"commit": "sha", "branch": "b", "dirty": True},
        )


def test_f3c_artifact_write_is_fail_closed(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "artifact.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already exists"):
        module._write_artifact(path, {"status": "pass"})
