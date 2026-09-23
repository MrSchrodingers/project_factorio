"""A checkpoint must not silently disinherit what the agent was carrying.

`GameState.to_raw()` serialises each inventory with `inventory.__dict__`, but
`fle.env.entities.Inventory` is a pydantic v2 model declared with
`extra="allow"`: the items live in `__pydantic_extra__` and `__dict__` is
empty. Measured on this machine:

    Inventory(**{'iron-plate': 3, 'coal': 5})
      __dict__            -> {}
      __pydantic_extra__  -> {'iron-plate': 3, 'coal': 5}

So a checkpoint written naively persists `"inventories": [{}]`, and restoring
from it hands the heir an empty pocket while reporting success. That is the
same failure shape that cost this project eleven generations: an absence
presented as a measurement.
"""

from __future__ import annotations

import json

import pytest

from factorio_ai_lab.learning.checkpoints import save_game_state


class _ExtraAllowInventory:
    """Stands in for pydantic v2 `extra="allow"`: items outside __dict__."""

    def __init__(self, items: dict[str, int]) -> None:
        self.__pydantic_extra__ = dict(items)


class _LosingGameState:
    """Reproduces the defect: to_raw() reports empty inventories."""

    def __init__(self, inventories: list[_ExtraAllowInventory]) -> None:
        self.inventories = inventories

    def to_raw(self) -> str:
        return json.dumps(
            {
                "entities": "encoded-entity-blob",
                # __dict__ of each inventory is empty, so this is what the
                # real serialiser produces.
                "inventories": [{} for _ in self.inventories],
            }
        )


def _written(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_defect_is_real_in_the_raw_payload() -> None:
    state = _LosingGameState([_ExtraAllowInventory({"iron-plate": 3})])
    assert json.loads(state.to_raw())["inventories"] == [{}]


def test_save_rebuilds_the_inventory_from_the_live_object(tmp_path) -> None:
    state = _LosingGameState(
        [_ExtraAllowInventory({"iron-plate": 3, "coal": 5})]
    )
    path = tmp_path / "champion.json"
    save_game_state(path, state)
    assert _written(path)["inventories"] == [{"iron-plate": 3, "coal": 5}]


def test_several_agents_keep_their_own_inventories(tmp_path) -> None:
    state = _LosingGameState(
        [
            _ExtraAllowInventory({"coal": 7}),
            _ExtraAllowInventory({"copper-plate": 2}),
        ]
    )
    path = tmp_path / "champion.json"
    save_game_state(path, state)
    assert _written(path)["inventories"] == [
        {"coal": 7},
        {"copper-plate": 2},
    ]


def test_an_empty_inventory_stays_empty(tmp_path) -> None:
    # Carrying nothing is a fact, not a defect: it must not be invented into
    # content, and it must not raise.
    state = _LosingGameState([_ExtraAllowInventory({})])
    path = tmp_path / "champion.json"
    save_game_state(path, state)
    assert _written(path)["inventories"] == [{}]


def test_a_payload_that_already_has_contents_is_left_alone(tmp_path) -> None:
    class _WorkingGameState(_LosingGameState):
        def to_raw(self) -> str:
            return json.dumps(
                {
                    "entities": "blob",
                    "inventories": [{"stone": 1}],
                }
            )

    state = _WorkingGameState([_ExtraAllowInventory({"iron-plate": 99})])
    path = tmp_path / "champion.json"
    save_game_state(path, state)
    # The serialiser reported content, so it is the authority: no overwrite.
    assert _written(path)["inventories"] == [{"stone": 1}]


def test_a_mismatched_length_is_not_guessed(tmp_path) -> None:
    class _Mismatched(_LosingGameState):
        def to_raw(self) -> str:
            return json.dumps({"entities": "blob", "inventories": [{}, {}]})

    state = _Mismatched([_ExtraAllowInventory({"coal": 1})])
    path = tmp_path / "champion.json"
    save_game_state(path, state)
    # One live inventory against two serialised slots: aligning them would be
    # a guess, so the payload is left as the serialiser produced it.
    assert _written(path)["inventories"] == [{}, {}]


def test_a_state_without_to_raw_is_refused(tmp_path) -> None:
    with pytest.raises(TypeError):
        save_game_state(tmp_path / "x.json", object())
