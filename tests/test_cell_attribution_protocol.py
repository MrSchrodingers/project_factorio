"""Changing what a rate counts is changing the instrument.

Stages 3, 5, 6, 7 and 8 used to read production from the world counter,
which in an inherited world sums the ancestor's machines as well. They now
read the cell the stage itself built. Measured in generation 46: the belt
smelting stage reports 7 plates of its own against 83 on the world counter
-- 91% of the old figure came from machines this generation did not build.

That is a better measurement and a smaller number. Comparing it against a
champion measured the old way would reject a challenger for a change of
instrument, which is precisely the failure `measurement_protocol` exists to
prevent: the incumbent becomes unbeatable for a reason that has nothing to
do with the factory.

The protocol identifier therefore moves. Rates measured per cell are
declared incommensurable with rates measured off the world counter, and the
floor is re-established by the first challenger promoted on the new
protocol.
"""

from __future__ import annotations

from factorio_ai_lab.learning.survival import (
    RATE_PROTOCOL_CELL_ATTRIBUTED,
    RATE_PROTOCOL_OBSERVED_WINDOW,
    FitnessVector,
    compare_challenger,
)

CAPS = frozenset({"iron_backbone"})


def _vector(protocol, rate):
    return FitnessVector(
        capabilities=CAPS,
        rates_per_s={"iron-plate": rate},
        rate_sources={"iron-plate": "endogenous_flow"},
        measurement_protocol=protocol,
        completed_stages=frozenset({"Belt-fed smelting"}),
        failed_stages=frozenset(),
    )


def test_the_two_protocols_are_distinct() -> None:
    assert RATE_PROTOCOL_CELL_ATTRIBUTED != RATE_PROTOCOL_OBSERVED_WINDOW


def test_a_smaller_rate_under_the_new_protocol_is_not_a_regression() -> None:
    # The champion's 0.487/s was measured off the world counter; the
    # challenger's 0.04/s is its own cell. Same factory would produce both.
    champion = _vector(RATE_PROTOCOL_OBSERVED_WINDOW, 0.487)
    challenger = _vector(RATE_PROTOCOL_CELL_ATTRIBUTED, 0.04)
    decision = compare_challenger(champion, challenger)
    assert not any("retention floor" in item for item in decision.regressions), (
        f"reprovou por troca de instrumento: {decision.regressions}"
    )


def test_the_incommensurability_is_declared() -> None:
    champion = _vector(RATE_PROTOCOL_OBSERVED_WINDOW, 0.487)
    challenger = _vector(RATE_PROTOCOL_CELL_ATTRIBUTED, 0.04)
    decision = compare_challenger(champion, challenger)
    reported = " ".join(decision.incommensurable_metrics)
    assert "rates_per_s" in reported, decision.incommensurable_metrics


def test_two_generations_on_the_same_protocol_still_compare() -> None:
    # The guard must not become permanent: once both sides use the new
    # protocol, a real drop is a real regression again.
    champion = _vector(RATE_PROTOCOL_CELL_ATTRIBUTED, 0.40)
    challenger = _vector(RATE_PROTOCOL_CELL_ATTRIBUTED, 0.04)
    decision = compare_challenger(champion, challenger)
    assert any("retention floor" in item for item in decision.regressions), (
        "a trava sumiu entre geracoes do mesmo protocolo"
    )


def test_the_runner_stamps_the_new_protocol() -> None:
    from factorio_ai_lab.learning.survival import fitness_from_research

    fitness = fitness_from_research(metrics={}, achieved=set())
    assert fitness.measurement_protocol == RATE_PROTOCOL_CELL_ATTRIBUTED
