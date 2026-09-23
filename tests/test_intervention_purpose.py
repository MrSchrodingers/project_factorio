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

Repair is the third case and the same trap. A repair step does carry material
by hand and does not remove future carrying, so it is not infrastructure; what
sets it apart is that it only runs after the graph has measured a machine as
broken, and the repair budget bounds how many may run. Generation 70 drove
fuel_starved_entities from 10 to 0 and paid four hand calls for it -- the
whole difference in manual logistics between generations 69 and 70 -- and was
rejected for the increase. The mechanism that fixed the factory was scored as
the factory getting worse.
"""

from __future__ import annotations

import pytest

from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    intervention_counts_from_code,
)
from factorio_ai_lab.learning.survival import (
    FitnessVector,
    compare_challenger,
    fitness_from_research,
)

OPERATION_CODE = "ore=extract_item(Prototype.IronOre,chest,quantity=10)\n"
INFRASTRUCTURE_CODE = (
    "feed_chest=place_entity(Prototype.WoodenChest,position=p)\n"
    "feed_chest=insert_item(Prototype.Coal,feed_chest,quantity=50)\n"
)
# The shape repair_refuel_code emits: draw the spare coal out of a world
# container, walk it to the machine the graph reported as starved.
REPAIR_CODE = (
    "repair_drawn+=extract_item(Prototype.Coal,Position(x=1,y=2),quantity=60)\n"
    "repair_inserted+=insert_item(Prototype.Coal,repair_machine,quantity=20)\n"
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


def test_repair_does_not_count_as_manual_logistics() -> None:
    executor = _executor()
    executor.execute(REPAIR_CODE, accept=_accept_everything, purpose="repair")
    snapshot = executor.intervention_snapshot()
    assert snapshot["committed"].get("manual_logistics_calls", 0) == 0, (
        "reparar o que quebrou nao pode contar como logistica manual de rotina"
    )


def test_repair_is_still_reported() -> None:
    # Reclassified, not hidden: a repair that disappears from the evidence is
    # indistinguishable from a repair that never happened.
    executor = _executor()
    executor.execute(REPAIR_CODE, accept=_accept_everything, purpose="repair")
    snapshot = executor.intervention_snapshot()
    assert snapshot["committed_repair"]["manual_logistics_calls"] == 2
    assert snapshot["attempted_repair"]["manual_logistics_calls"] == 2


def test_a_rejected_repair_is_attempted_but_not_committed() -> None:
    executor = _executor()
    executor.execute(REPAIR_CODE, accept=lambda result: False, purpose="repair")
    snapshot = executor.intervention_snapshot()
    assert snapshot["attempted_repair"]["manual_logistics_calls"] == 2
    assert snapshot["committed_repair"].get("manual_logistics_calls", 0) == 0


def test_the_three_streams_do_not_mix() -> None:
    executor = _executor()
    executor.execute(OPERATION_CODE, accept=_accept_everything)
    executor.execute(
        INFRASTRUCTURE_CODE,
        accept=_accept_everything,
        purpose="infrastructure",
    )
    executor.execute(REPAIR_CODE, accept=_accept_everything, purpose="repair")
    snapshot = executor.intervention_snapshot()
    assert snapshot["committed"]["manual_logistics_calls"] == 1
    assert snapshot["committed_infrastructure"]["manual_logistics_calls"] == 1
    assert snapshot["committed_repair"]["manual_logistics_calls"] == 2


def _fitness_with(interventions: dict[str, dict[str, int]]) -> FitnessVector:
    return fitness_from_research(
        metrics={"interventions": interventions},
        achieved={"coal_mining"},
    )


def test_the_fitness_vector_reads_the_repair_count_apart() -> None:
    vector = _fitness_with(
        {
            "committed": {"manual_logistics_calls": 69},
            "committed_repair": {"manual_logistics_calls": 4},
        }
    )
    assert vector.manual_logistics_calls == 69
    assert vector.repair_logistics_calls == 4


def test_no_repair_accounting_is_not_a_repair_count_of_zero() -> None:
    # Every generation before the repair loop existed recorded nothing here.
    # Reading that as "repaired nothing" would let an absence answer a
    # question nobody measured.
    vector = _fitness_with({"committed": {"manual_logistics_calls": 69}})
    assert vector.repair_logistics_calls is None


def test_the_repair_count_survives_a_round_trip() -> None:
    vector = _fitness_with(
        {
            "committed": {"manual_logistics_calls": 69},
            "committed_repair": {"manual_logistics_calls": 4},
        }
    )
    assert FitnessVector.from_dict(vector.to_dict()).repair_logistics_calls == 4


def test_the_repair_is_not_charged_to_the_manual_logistics_comparison() -> None:
    """Generation 70 against the incumbent, with and without the separation.

    The pair is the whole argument: the same world, the same repair, booked
    two ways. Booked as operation it reads as an increase and rejects; booked
    apart it does not. Asserting only the second half would pass even if the
    separation did nothing.
    """
    champion = FitnessVector(
        capabilities=frozenset({"coal_mining"}),
        rates_per_s={"coal": 0.05},
        manual_logistics_calls=51,
        autonomy_score=0.125,
    )
    booked_as_operation = FitnessVector(
        capabilities=champion.capabilities,
        rates_per_s=dict(champion.rates_per_s),
        manual_logistics_calls=55,
        autonomy_score=0.250,
    )
    booked_as_repair = FitnessVector(
        capabilities=champion.capabilities,
        rates_per_s=dict(champion.rates_per_s),
        manual_logistics_calls=51,
        repair_logistics_calls=4,
        autonomy_score=0.250,
    )

    charged = compare_challenger(champion, booked_as_operation)
    apart = compare_challenger(champion, booked_as_repair)

    assert not charged.promoted, "o caso nao exerce a regra"
    assert charged.regressions, "o caso nao exerce a regra"
    assert apart.regressions == (), f"reparo ainda cobrado: {apart.regressions}"
    assert apart.promoted


def test_the_repair_count_alone_never_rejects() -> None:
    # A challenger that had to repair more than the incumbent is not thereby
    # worse: the repair answers a reading the graph already took, and the
    # repair budget is what bounds it. The axis carries no verdict.
    champion = FitnessVector(
        capabilities=frozenset({"coal_mining"}),
        rates_per_s={"coal": 0.05},
        manual_logistics_calls=51,
        repair_logistics_calls=0,
        autonomy_score=0.125,
    )
    challenger = FitnessVector(
        capabilities=champion.capabilities,
        rates_per_s=dict(champion.rates_per_s),
        manual_logistics_calls=51,
        repair_logistics_calls=12,
        autonomy_score=0.250,
    )
    decision = compare_challenger(champion, challenger)
    assert decision.regressions == (), f"reprovou pelo reparo: {decision.regressions}"
