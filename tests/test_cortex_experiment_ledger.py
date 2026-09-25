from __future__ import annotations

import ast
from pathlib import Path

from factorio_ai_lab.cortex.experiment_ledger import (
    CREDIT_NOT_EXECUTED,
    CREDIT_RECORDED_MISMATCH,
    CREDIT_UNMEASURED,
    CREDIT_VERIFIED,
    ExecutiveExperimentLedger,
    build_episode_record,
    observed_episode_from_repair_row,
)

ROOT = Path(__file__).parents[1]


def _row(
    *,
    executed: bool = True,
    before: float | None = 13.0,
    after: float | None = 5.0,
    verdict: str | None = "held",
    reward: float | None = 1.0,
) -> dict:
    return {
        "run_id": "legacy-run",
        "generation": 93,
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
            "verdict": verdict,
            "reward": reward,
        },
    }


def _record(**kwargs):
    episode = observed_episode_from_repair_row(
        _row(**kwargs),
        source="runs/repairs.jsonl",
        source_sha256="sha",
        row_index=7,
    )
    return build_episode_record(episode)


def test_verified_measured_episode_receives_credit() -> None:
    record = _record()

    assert record.verification.outcome.before == 13.0
    assert record.verification.outcome.after == 5.0
    assert record.verification.outcome.verdict == "held"
    assert record.verification.matches_recorded is True
    assert record.credit.eligible is True
    assert record.credit.reward == 1.0
    assert record.credit.reason == CREDIT_VERIFIED


def test_unexecuted_episode_never_receives_credit() -> None:
    record = _record(executed=False)

    assert record.verification.matches_recorded is True
    assert record.credit.eligible is False
    assert record.credit.reward is None
    assert record.credit.reason == CREDIT_NOT_EXECUTED


def test_unmeasured_episode_never_becomes_zero_reward() -> None:
    record = _record(
        before=None,
        after=None,
        verdict="unmeasured",
        reward=None,
    )

    assert record.verification.outcome.reward is None
    assert record.credit.eligible is False
    assert record.credit.reward is None
    assert record.credit.reason == CREDIT_UNMEASURED


def test_recorded_outcome_mismatch_fails_closed() -> None:
    record = _record(verdict="did_not_hold", reward=0.0)

    assert record.verification.outcome.verdict == "held"
    assert record.verification.matches_recorded is False
    assert record.credit.eligible is False
    assert record.credit.reward is None
    assert record.credit.reason == CREDIT_RECORDED_MISMATCH


def test_sqlite_episode_ledger_is_persistent_and_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "episodes.sqlite3"
    record = _record()

    with ExecutiveExperimentLedger(path) as ledger:
        assert ledger.append(record) == "inserted"
        assert ledger.append(record) == "already_present"
        assert ledger.quick_check() == "ok"
        assert ledger.count() == 1
        digest = ledger.episode_digest(record.episode_id)
        assert digest == record.payload_sha256

    with ExecutiveExperimentLedger(path) as reopened:
        assert reopened.quick_check() == "ok"
        assert reopened.count() == 1
        assert reopened.episode_digest(record.episode_id) == record.payload_sha256


def test_experiment_ledger_has_no_live_execution_import() -> None:
    path = ROOT / "src/factorio_ai_lab/cortex/experiment_ledger.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    forbidden = {
        "factorio_ai_lab.cortex.option_execute",
        "factorio_ai_lab.cortex.structural_execute",
        "factorio_ai_lab.experiments.curriculum_runner",
        "factorio_ai_lab.integrations.fle",
        "factorio_rcon",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not (imports & forbidden)
