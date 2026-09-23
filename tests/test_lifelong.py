import base64
import json
import pickle
import zlib
from pathlib import Path

import pytest

from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor
from factorio_ai_lab.learning.lifelong import (
    InheritanceLedger,
    LifelongWarmStart,
    attribute_generation,
    capture_champion_state,
    decode_entity_state,
    inheritance_path,
    inventory_items,
    load_champion_state,
    summarize_state,
)


def encode_entities(entities: list[dict]) -> str:
    return base64.b64encode(
        zlib.compress(json.dumps(entities).encode("utf-8"))
    ).decode("utf-8")


QUOTED_ENTITIES = [
    {"name": '"burner-mining-drill"', "position": {"x": "1", "y": "2"}},
    {"name": '"stone-furnace"', "position": {"x": "3", "y": "4"}},
    {"name": '"stone-furnace"', "position": {"x": "5", "y": "6"}},
    {"name": '"character"', "position": {"x": "0", "y": "0"}},
]

BARE_ENTITIES = [
    {"name": "burner-mining-drill", "position": {"x": "1", "y": "2"}},
    {"name": "stone-furnace", "position": {"x": "3", "y": "4"}},
    {"name": "stone-furnace", "position": {"x": "5", "y": "6"}},
    {"name": "character", "position": {"x": "0", "y": "0"}},
]


def technology(name: str, researched: bool) -> dict:
    return {
        "name": name,
        "researched": researched,
        "enabled": True,
        "level": 1,
        "research_unit_count": 10,
        "research_unit_energy": 30.0,
        "prerequisites": [],
        "ingredients": [{"automation-science-pack": 1}],
    }


RESEARCH = {
    "technologies": {
        "automation": technology("automation", True),
        "logistics": technology("logistics", False),
    },
    "current_research": None,
    "research_progress": 0.0,
    "research_queue": [],
    "progress": {},
}


class FakeInventory:
    """Reproduces fle.env.entities.Inventory.

    It is a pydantic v2 model with extra="allow", so the item counts live in
    __pydantic_extra__ and GameState.to_raw (which reads __dict__) emits an
    empty object.
    """

    def __init__(self, **items: int) -> None:
        self.__pydantic_extra__ = dict(items)

    def to_raw_view(self) -> dict:
        return {}


class FakeGameState:
    def __init__(
        self,
        *,
        entities: str,
        inventories: list,
        research: dict | None = None,
        namespaces: list[str] | None = None,
    ) -> None:
        self.entities = entities
        self.inventories = inventories
        self.research = research
        self.namespaces = namespaces

    def to_raw(self) -> str:
        payload = {
            "entities": self.entities,
            "inventories": [
                inventory.to_raw_view()
                if hasattr(inventory, "to_raw_view")
                else inventory
                for inventory in self.inventories
            ],
            "timestamp": 1.0,
            "namespaces": (
                self.namespaces
                if self.namespaces is not None
                else [pickle.dumps({"center": "x"}).hex()]
            ),
            "agent_messages": [[]],
        }
        if self.research is not None:
            payload["research"] = self.research
        return json.dumps(payload)


def champion_state() -> FakeGameState:
    return FakeGameState(
        entities=encode_entities(QUOTED_ENTITIES),
        inventories=[FakeInventory(**{"iron-plate": 12, "coal": 3})],
        research=RESEARCH,
    )


def test_decode_entity_state_accepts_compressed_plain_and_decoded() -> None:
    compressed = decode_entity_state(encode_entities(BARE_ENTITIES))
    plain = decode_entity_state(
        base64.b64encode(json.dumps(BARE_ENTITIES).encode("utf-8")).decode("utf-8")
    )
    already_decoded = decode_entity_state(BARE_ENTITIES)

    assert compressed == BARE_ENTITIES
    assert plain == BARE_ENTITIES
    assert already_decoded == BARE_ENTITIES
    assert decode_entity_state(None) == []
    assert decode_entity_state("") == []


def test_summarize_state_normalizes_lua_quoted_entity_names() -> None:
    ledger = summarize_state(champion_state())

    assert ledger.entities == {"burner-mining-drill": 1, "stone-furnace": 2}
    assert ledger.entity_total == 3
    assert ledger.researched == ("automation",)
    assert ledger.is_empty is False


def test_summarize_state_agrees_on_bare_and_quoted_names() -> None:
    quoted = summarize_state(champion_state())
    bare = summarize_state(
        FakeGameState(
            entities=encode_entities(BARE_ENTITIES),
            inventories=[FakeInventory(**{"iron-plate": 12, "coal": 3})],
            research=RESEARCH,
        )
    )

    assert quoted.entities == bare.entities
    assert quoted.entity_total == bare.entity_total


def test_summarize_state_of_empty_world_is_empty() -> None:
    ledger = summarize_state(
        FakeGameState(entities=encode_entities([]), inventories=[{}])
    )

    assert ledger.is_empty is True
    assert ledger.entity_total == 0


def test_inventory_items_reads_pydantic_extras_and_mappings() -> None:
    assert inventory_items(FakeInventory(**{"iron-plate": 4})) == {"iron-plate": 4}
    assert inventory_items({"coal": 2, "wood": 0}) == {"coal": 2}
    assert inventory_items(None) == {}


def test_capture_recovers_inventories_that_to_raw_drops(tmp_path: Path) -> None:
    path = tmp_path / "lifelong_champion_state.json"

    record = capture_champion_state(path, champion_state(), run_id="run-1")

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["inventories"] == [{"iron-plate": 12, "coal": 3}]
    assert record["ledger"]["inventories"] == {"coal": 3, "iron-plate": 12}
    assert record["ledger"]["inventory_total"] == 15


def test_capture_strips_pickled_namespaces(tmp_path: Path) -> None:
    path = tmp_path / "lifelong_champion_state.json"

    capture_champion_state(path, champion_state(), run_id="run-1")

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["namespaces"] == [""]


def test_capture_writes_provenance_sidecar(tmp_path: Path) -> None:
    path = tmp_path / "lifelong_champion_state.json"

    record = capture_champion_state(
        path,
        champion_state(),
        run_id="curriculum-1",
        generation=7,
        seed=99,
        reason="survived the curriculum",
    )

    sidecar = json.loads(inheritance_path(path).read_text(encoding="utf-8"))
    assert sidecar["source"]["run_id"] == "curriculum-1"
    assert sidecar["source"]["generation"] == 7
    assert sidecar["source"]["seed"] == 99
    assert sidecar["ledger"]["entity_total"] == 3
    assert sidecar["checkpoint"]["sha256"] == record["checkpoint"]["sha256"]


def test_load_champion_state_returns_none_when_no_champion_survived(
    tmp_path: Path,
) -> None:
    assert load_champion_state(tmp_path / "lifelong_champion_state.json") is None


def test_load_champion_state_round_trips_through_fle(tmp_path: Path) -> None:
    pytest.importorskip("fle.commons.models.game_state")
    path = tmp_path / "lifelong_champion_state.json"
    capture_champion_state(path, champion_state(), run_id="curriculum-1")

    inheritance = load_champion_state(path)

    assert inheritance is not None
    assert inheritance.ledger.entities == {
        "burner-mining-drill": 1,
        "stone-furnace": 2,
    }
    assert inheritance.ledger.inventories == {"coal": 3, "iron-plate": 12}
    assert inheritance.ledger.researched == ("automation",)
    assert inheritance.provenance["run_id"] == "curriculum-1"
    record = inheritance.to_record()
    assert "game_state" not in record
    assert json.loads(json.dumps(record))["available"] is True


class FakeExecutor:
    def __init__(self) -> None:
        self.game_state = None
        self.resets: list[tuple[int | None, object]] = []

    def reset(self, *, seed: int | None = None, game_state: object = None) -> object:
        self.resets.append((seed, game_state))
        self.game_state = game_state
        return game_state


def test_warm_start_injects_inherited_state_into_first_reset_only() -> None:
    inherited = champion_state()
    executor = FakeExecutor()

    with LifelongWarmStart(inherited, executor_class=FakeExecutor) as warm_start:
        executor.reset(seed=1)
        executor.reset(seed=2)

    assert warm_start.applied is True
    assert executor.resets[0] == (1, inherited)
    assert executor.resets[1] == (2, None)


def test_warm_start_without_inheritance_leaves_reset_untouched() -> None:
    executor = FakeExecutor()

    with LifelongWarmStart(None, executor_class=FakeExecutor) as warm_start:
        executor.reset(seed=1)

    assert warm_start.applied is False
    assert executor.resets == [(1, None)]


def test_warm_start_restores_the_original_reset_on_exit() -> None:
    original = FakeExecutor.reset
    executor = FakeExecutor()

    with LifelongWarmStart(champion_state(), executor_class=FakeExecutor):
        executor.reset(seed=1)

    assert FakeExecutor.reset is original
    executor.reset(seed=3)
    assert executor.resets[-1] == (3, None)


def test_warm_start_restores_the_original_reset_after_a_failed_generation() -> None:
    original = FakeExecutor.reset

    with (
        pytest.raises(RuntimeError),
        LifelongWarmStart(champion_state(), executor_class=FakeExecutor),
    ):
        raise RuntimeError("generation exploded")

    assert FakeExecutor.reset is original


def test_warm_start_exposes_the_final_world_state() -> None:
    inherited = champion_state()
    grown = champion_state()
    executor = FakeExecutor()

    with LifelongWarmStart(inherited, executor_class=FakeExecutor) as warm_start:
        executor.reset(seed=1)
        executor.game_state = grown

    assert warm_start.final_game_state() is grown
    assert warm_start.to_record() == {"warm_started": True, "executors_reset": 1}


def ledger(entities: dict[str, int], researched: tuple[str, ...] = ()) -> InheritanceLedger:
    return InheritanceLedger(
        entities=dict(entities),
        entity_total=sum(entities.values()),
        inventories={},
        inventory_total=0,
        researched=researched,
        agents=1,
    )


def test_attribute_generation_separates_inherited_from_built() -> None:
    inherited = ledger({"stone-furnace": 2}, ("automation",))
    final = ledger({"stone-furnace": 3, "transport-belt": 10}, ("automation", "logistics"))

    report = attribute_generation(inherited, final)

    assert report["built"]["entities"] == {"stone-furnace": 1, "transport-belt": 10}
    assert report["built"]["entity_total"] == 11
    assert report["built"]["researched"] == ["logistics"]
    assert report["removed"]["entities"] == {}
    assert report["evidence_contaminated"] is True


def test_attribute_generation_reports_destroyed_inheritance() -> None:
    inherited = ledger({"stone-furnace": 4})
    final = ledger({"stone-furnace": 1})

    report = attribute_generation(inherited, final)

    assert report["built"]["entities"] == {}
    assert report["removed"]["entities"] == {"stone-furnace": 3}


def test_attribute_generation_of_a_cold_start_is_not_contaminated() -> None:
    report = attribute_generation(None, ledger({"stone-furnace": 2}))

    assert report["evidence_contaminated"] is False
    assert report["built"]["entities"] == {"stone-furnace": 2}


class RecordingEnvironment:
    """Minimal stand-in for the FLE gym environment.

    It records exactly what ``TransactionalFLEExecutor.reset`` forwards, which
    is the call FLE turns into ``FactorioInstance.reset(game_state)``.
    """

    def __init__(self) -> None:
        self.resets: list[tuple[object, int | None]] = []

    def reset(self, *, options: dict | None = None, seed: int | None = None) -> dict:
        game_state = None if options is None else options.get("game_state")
        self.resets.append((game_state, seed))
        return {}

    def step(self, action: object) -> tuple:
        raise AssertionError("the warm start must not step the environment")

    def close(self) -> None:
        return None


def test_warm_start_reaches_the_real_transactional_executor() -> None:
    environment = RecordingEnvironment()
    executor = TransactionalFLEExecutor(environment)
    inherited = champion_state()

    with LifelongWarmStart(inherited) as warm_start:
        executor.reset(seed=7)
        assert executor.game_state is inherited
        executor.reset(seed=8)

    assert warm_start.applied is True
    assert environment.resets[0][0] is inherited
    assert environment.resets[0][1] == 7
    assert environment.resets[1] == (None, 8)
