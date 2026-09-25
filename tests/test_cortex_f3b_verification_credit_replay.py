from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/run_cortex_f3b_verification_credit_replay.py"


def _module():
    spec = importlib.util.spec_from_file_location("f3b_replay", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(
    *,
    run_id: str,
    before: float | None,
    after: float | None,
    executed: bool,
    reward: float | None,
    verdict: str,
) -> dict:
    return {
        "run_id": run_id,
        "generation": 1,
        "stage": "Logistic science",
        "symptom": "fuel_starved:no_fuel",
        "action_key": "resupply:insert_fuel_from_world_container",
        "executed": executed,
        "targets": ["u1"],
        "outcome": {
            "prediction": {
                "metric": "fuel_starved_entities",
                "direction": "decrease",
            },
            "before": before,
            "after": after,
            "reward": reward,
            "verdict": verdict,
        },
    }


def test_f3b_replay_persists_only_measured_executed_evidence(
    tmp_path: Path,
) -> None:
    repairs = tmp_path / "runs" / "repairs.jsonl"
    repairs.parent.mkdir(parents=True)
    rows = [
        _row(
            run_id="r1",
            before=13.0,
            after=5.0,
            executed=True,
            reward=1.0,
            verdict="held",
        ),
        _row(
            run_id="r2",
            before=13.0,
            after=13.0,
            executed=True,
            reward=0.0,
            verdict="did_not_hold",
        ),
        _row(
            run_id="r3",
            before=None,
            after=None,
            executed=True,
            reward=None,
            verdict="unmeasured",
        ),
        _row(
            run_id="r4",
            before=13.0,
            after=5.0,
            executed=False,
            reward=1.0,
            verdict="held",
        ),
    ]
    repairs.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    module = _module()
    ledger = tmp_path / "runs" / "ledger" / "episodes.sqlite3"

    payload = module.build_replay(
        root=tmp_path,
        repairs_path=repairs,
        ledger_path=ledger,
        revision={
            "commit": "sha",
            "branch": "research/cortex-v1",
            "dirty": False,
        },
    )

    assert payload["status"] == "pass"
    assert payload["verification"]["selected_episode_count"] == 2
    assert payload["verification"]["held"] == 1
    assert payload["verification"]["missed"] == 1
    assert payload["verification"]["unmeasured"] == 0
    assert payload["credit"]["eligible"] == 2
    assert payload["credit"]["reward_sum"] == 1.0
    assert payload["ledger"]["episode_count"] == 2
    assert payload["ledger"]["inserted"] == 2
    assert payload["ledger"]["quick_check"] == "ok"
    assert len(payload["ledger"]["episode_digests"]) == 2
    assert payload["world_mutation"] is False
    assert payload["factorio_rcon_used"] is False
    assert payload["fle_environment_created"] is False
    assert payload["execution_grant_created"] is False


def test_f3b_replay_is_idempotent_at_ledger_layer(tmp_path: Path) -> None:
    repairs = tmp_path / "runs" / "repairs.jsonl"
    repairs.parent.mkdir(parents=True)
    repairs.write_text(
        json.dumps(
            _row(
                run_id="r1",
                before=13.0,
                after=5.0,
                executed=True,
                reward=1.0,
                verdict="held",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    module = _module()
    ledger = tmp_path / "runs" / "ledger" / "episodes.sqlite3"
    revision = {"commit": "sha", "branch": "b", "dirty": False}

    first = module.build_replay(
        root=tmp_path,
        repairs_path=repairs,
        ledger_path=ledger,
        revision=revision,
    )
    second = module.build_replay(
        root=tmp_path,
        repairs_path=repairs,
        ledger_path=ledger,
        revision=revision,
    )

    assert first["ledger"]["inserted"] == 1
    assert second["ledger"]["inserted"] == 0
    assert second["ledger"]["already_present"] == 1
    assert first["ledger"]["episode_digests"] == second["ledger"]["episode_digests"]


def test_f3b_replay_refuses_dirty_revision(tmp_path: Path) -> None:
    repairs = tmp_path / "runs" / "repairs.jsonl"
    repairs.parent.mkdir(parents=True)
    repairs.write_text("{}\n", encoding="utf-8")
    module = _module()

    with pytest.raises(RuntimeError, match="clean source tree"):
        module.build_replay(
            root=tmp_path,
            repairs_path=repairs,
            ledger_path=tmp_path / "episodes.sqlite3",
            revision={"commit": "sha", "branch": "b", "dirty": True},
        )


def test_canonical_artifact_write_is_fail_closed(tmp_path: Path) -> None:
    module = _module()
    path = tmp_path / "artifact.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already exists"):
        module._write_artifact(path, {"status": "pass"})
