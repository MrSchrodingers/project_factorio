from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/run_cortex_f3a_shadow_replay.py"


def _module():
    spec = importlib.util.spec_from_file_location("f3a_replay", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_f3a_replay_separates_observed_evidence_from_counterfactuals(
    tmp_path: Path,
) -> None:
    repairs = tmp_path / "runs" / "repairs.jsonl"
    repairs.parent.mkdir(parents=True)
    repairs.write_text(
        json.dumps(
            {
                "run_id": "legacy-run",
                "generation": 9,
                "stage": "Logistic science",
                "symptom": (
                    "producer_output_unprocessed:"
                    "output_buffered_not_processed"
                ),
                "action_key": (
                    "placement:place_processing_for_buffered_output"
                ),
                "executed": False,
                "targets": ["u1", "u2"],
                "outcome": {"reward": None, "verdict": "unmeasured"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    module = _module()
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
    assert payload["authority"] == "shadow"
    assert payload["world_mutation"] is False
    assert payload["factorio_rcon_used"] is False
    assert payload["fle_environment_created"] is False
    assert payload["world_lease_acquired"] is False
    assert payload["execution_grant_created"] is False
    assert payload["continuous_authority"] is False
    assert payload["observed_evidence"]["run_id"] == "legacy-run"
    assert payload["counterfactual_expansion"]["observed_in_world"] is False
    assert payload["counterfactual_expansion"]["candidate_count"] == 2
    assert payload["checks"]["same_goal_same_candidate_set"] is True
    assert payload["checks"]["different_policy_different_choice"] is True
    assert payload["checks"]["predictions_exist_before_action"] is True
    assert "F3 Exit Gate completion" in payload["claim_boundary"]["does_not_prove"]


def test_f3a_replay_refuses_dirty_revision(tmp_path: Path) -> None:
    repairs = tmp_path / "runs" / "repairs.jsonl"
    repairs.parent.mkdir(parents=True)
    repairs.write_text("{}\n", encoding="utf-8")
    module = _module()

    try:
        module.build_replay(
            root=tmp_path,
            repairs_path=repairs,
            revision={"commit": "sha", "branch": "b", "dirty": True},
        )
    except RuntimeError as exc:
        assert "clean source tree" in str(exc)
    else:
        raise AssertionError("dirty F3-A replay was not refused")
