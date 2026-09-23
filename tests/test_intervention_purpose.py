"""Building automation must not score as carrying items by hand.

The intervention counters measure how much the agent has to move by hand
because the factory cannot move it itself. They are derived from the text of
the step (`insert_item(`, `extract_item(` ...), so they cannot tell apart two
opposite things: feeding a furnace by hand every cycle, and loading once the
chest that feeds an automatic inserter forever.

Counting the second as manual logistics makes installing automation register
as a regression in survival.py, which is exactly backwards. Infrastructure
steps are therefore counted apart and reported apart -- reclassified, never
hidden.
"""

from __future__ import annotations

import pytest

from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    intervention_counts_from_code,
)

OPERATION_CODE = "ore=extract_item(Prototype.IronOre,chest,quantity=10)\n"
INFRASTRUCTURE_CODE = (
    "feed_chest=place_entity(Prototype.WoodenChest,position=p)\n"
    "feed_chest=insert_item(Prototype.Coal,feed_chest,quantity=50)\n"
)


class _StubEnvironment:
    """Minimal environment: every step succeeds and returns a new state."""

    def __init__(self) -> None:
        self.state = "initial"
        self.steps = 0

    def reset(self, *, options=None, seed=None):
        self.state = (options or {}).get("game_state") or "initial"
        return self.state

    def step(self, action):
        self.steps += 1
        info = {"error_occurred": False, "output_game_state": f"state-{self.steps}"}
        return "observation", 1.0, False, False, info

    def close(self) -> None:
        return None


def _executor() -> TransactionalFLEExecutor:
    return TransactionalFLEExecutor(
        environment=_StubEnvironment(),
        action_factory=lambda idx, code, state: (idx, code, state),
    )


def _accept_everything(result) -> bool:
    return True


def test_the_counter_cannot_tell_the_two_apart_on_its_own() -> None:
    # Both texts contain one insert/extract call, which is precisely why the
    # distinction has to be declared by the caller.
    assert intervention_counts_from_code(OPERATION_CODE)["manual_logistics_calls"] == 1
    assert (
        intervention_counts_from_code(INFRASTRUCTURE_CODE)["manual_logistics_calls"]
        == 1
    )


def test_operation_counts_as_manual_logistics() -> None:
    executor = _executor()
    executor.execute(OPERATION_CODE, accept=_accept_everything)
    snapshot = executor.intervention_snapshot()
    assert snapshot["committed"]["manual_logistics_calls"] == 1
    assert snapshot["committed_infrastructure"].get("manual_logistics_calls", 0) == 0


def test_infrastructure_does_not_count_as_manual_logistics() -> None:
    executor = _executor()
    executor.execute(
        INFRASTRUCTURE_CODE,
        accept=_accept_everything,
        purpose="infrastructure",
    )
    snapshot = executor.intervention_snapshot()
    assert snapshot["committed"].get("manual_logistics_calls", 0) == 0, (
        "instalar automacao nao pode contar como logistica manual"
    )


def test_infrastructure_is_still_reported() -> None:
    # Reclassified, not hidden: the calls must remain visible somewhere.
    executor = _executor()
    executor.execute(
        INFRASTRUCTURE_CODE,
        accept=_accept_everything,
        purpose="infrastructure",
    )
    snapshot = executor.intervention_snapshot()
    assert snapshot["committed_infrastructure"]["manual_logistics_calls"] == 1
    assert snapshot["attempted_infrastructure"]["manual_logistics_calls"] == 1


def test_rejected_infrastructure_is_attempted_but_not_committed() -> None:
    executor = _executor()
    executor.execute(
        INFRASTRUCTURE_CODE,
        accept=lambda result: False,
        purpose="infrastructure",
    )
    snapshot = executor.intervention_snapshot()
    assert snapshot["attempted_infrastructure"]["manual_logistics_calls"] == 1
    assert snapshot["committed_infrastructure"].get("manual_logistics_calls", 0) == 0


def test_the_two_streams_do_not_mix() -> None:
    executor = _executor()
    executor.execute(OPERATION_CODE, accept=_accept_everything)
    executor.execute(
        INFRASTRUCTURE_CODE,
        accept=_accept_everything,
        purpose="infrastructure",
    )
    snapshot = executor.intervention_snapshot()
    assert snapshot["committed"]["manual_logistics_calls"] == 1
    assert snapshot["committed_infrastructure"]["manual_logistics_calls"] == 1


def test_an_unknown_purpose_is_refused() -> None:
    # A typo must not silently fall back to counting as operation.
    executor = _executor()
    with pytest.raises(ValueError, match="purpose"):
        executor.execute(
            OPERATION_CODE,
            accept=_accept_everything,
            purpose="infrastruture",
        )
