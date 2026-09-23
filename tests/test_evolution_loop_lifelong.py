import json
from pathlib import Path

import pytest
from test_lifelong import FakeExecutor, champion_state

from factorio_ai_lab.experiments import evolution_loop
from factorio_ai_lab.learning.lifelong import LifelongWarmStart


def promoted_lab(promoted: bool) -> dict:
    return {
        "run_id": "curriculum-20260922T000000Z",
        "status": "generation_complete",
        "evolution": {
            "generation": 7,
            "promotion": {
                "promoted": promoted,
                "reason": "challenger beat the incumbent",
            },
        },
    }


def use_temporary_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "lifelong_champion_state.json"
    monkeypatch.setattr(evolution_loop, "LIFELONG_CHECKPOINT", path)
    return path


def test_promoted_generation_persists_the_surviving_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = use_temporary_checkpoint(tmp_path, monkeypatch)

    record = evolution_loop._capture_lifelong_state(
        game_state=champion_state(),
        lab=promoted_lab(True),
        seed=42,
    )

    assert record["captured"] is True
    assert path.exists()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["inventories"] == [{"iron-plate": 12, "coal": 3}]
    assert record["ledger"]["entity_total"] == 3
    assert record["source"]["seed"] == 42
    assert record["source"]["generation"] == 7


def test_rejected_generation_does_not_persist_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = use_temporary_checkpoint(tmp_path, monkeypatch)

    record = evolution_loop._capture_lifelong_state(
        game_state=champion_state(),
        lab=promoted_lab(False),
        seed=42,
    )

    assert record["captured"] is False
    assert record["reason"] == "not_promoted"
    assert not path.exists()


def test_generation_without_world_state_does_not_persist_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = use_temporary_checkpoint(tmp_path, monkeypatch)

    record = evolution_loop._capture_lifelong_state(
        game_state=None,
        lab=promoted_lab(True),
        seed=42,
    )

    assert record["captured"] is False
    assert record["reason"] == "no_world_state"
    assert not path.exists()


def test_first_generation_reports_that_there_is_nothing_to_inherit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_temporary_checkpoint(tmp_path, monkeypatch)

    inheritance, record = evolution_loop._load_lifelong_inheritance(True)

    assert inheritance is None
    assert record["available"] is False
    assert record["reason"] == "no_champion_state"


def test_inheritance_can_be_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = use_temporary_checkpoint(tmp_path, monkeypatch)
    evolution_loop._capture_lifelong_state(
        game_state=champion_state(),
        lab=promoted_lab(True),
        seed=42,
    )
    assert path.exists()

    inheritance, record = evolution_loop._load_lifelong_inheritance(False)

    assert inheritance is None
    assert record["available"] is False
    assert record["reason"] == "disabled"


def test_next_generation_inherits_the_persisted_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fle.commons.models.game_state")
    use_temporary_checkpoint(tmp_path, monkeypatch)
    evolution_loop._capture_lifelong_state(
        game_state=champion_state(),
        lab=promoted_lab(True),
        seed=42,
    )

    inheritance, record = evolution_loop._load_lifelong_inheritance(True)

    assert inheritance is not None
    assert record["available"] is True
    assert record["ledger"]["entities"] == {
        "burner-mining-drill": 1,
        "stone-furnace": 2,
    }
    # The loop state is written with atomic_json: the record must stay small
    # and JSON-safe, never carrying the multi-megabyte world payload.
    assert "game_state" not in record
    assert json.loads(json.dumps(record, default=str))["available"] is True

    executor = FakeExecutor()
    with LifelongWarmStart(
        inheritance.game_state,
        executor_class=FakeExecutor,
    ) as warm_start:
        executor.reset(seed=43)

    assert warm_start.applied is True
    assert executor.resets[0][1] is inheritance.game_state


def test_attribution_separates_inherited_capability_from_built_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fle.commons.models.game_state")
    use_temporary_checkpoint(tmp_path, monkeypatch)
    evolution_loop._capture_lifelong_state(
        game_state=champion_state(),
        lab=promoted_lab(True),
        seed=42,
    )
    inheritance, _ = evolution_loop._load_lifelong_inheritance(True)

    executor = FakeExecutor()
    with LifelongWarmStart(
        inheritance.game_state,
        executor_class=FakeExecutor,
    ) as warm_start:
        executor.reset(seed=43)

    final_state, report = evolution_loop._lifelong_attribution(
        inheritance,
        warm_start,
    )

    assert final_state is inheritance.game_state
    # A generation that only inherited built nothing of its own.
    assert report["built"]["entities"] == {}
    assert report["evidence_contaminated"] is True
    assert report["warm_start"]["warm_started"] is True


def test_attribution_of_a_cold_generation_is_clean() -> None:
    executor = FakeExecutor()
    with LifelongWarmStart(None, executor_class=FakeExecutor) as warm_start:
        executor.reset(seed=1)
        executor.game_state = champion_state()

    final_state, report = evolution_loop._lifelong_attribution(None, warm_start)

    assert final_state is not None
    assert report["evidence_contaminated"] is False
    assert report["built"]["entities"] == {
        "burner-mining-drill": 1,
        "stone-furnace": 2,
    }
    assert report["warm_start"]["warm_started"] is False


def test_corrupt_checkpoint_does_not_abort_the_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fle.commons.models.game_state")
    path = use_temporary_checkpoint(tmp_path, monkeypatch)
    path.write_text("{}", encoding="utf-8")

    inheritance, record = evolution_loop._load_lifelong_inheritance(True)

    assert inheritance is None
    assert record["available"] is False
    assert record["reason"] == "load_error"
    assert "error" in record
