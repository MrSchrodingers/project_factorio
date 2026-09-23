"""Rates measured by different instruments must not be compared.

The stages used to divide output by the literal passed to sleep(), while the
game kept running through move_to, extract_item and place_entity as well. The
real window was several times the literal, so every rate was inflated by that
factor. The generation-6 champion records copper-ore at 0.8125/s; a burner
mining drill mines 0.25 ore/s, so that figure is 3.25x what the machine can
physically do.

Once the denominator is fixed, honest rates drop by roughly that factor, and a
retention floor derived from the inflated numbers becomes unreachable. Keeping
the floor would make the incumbent unbeatable for a reason that has nothing to
do with the factory, so a protocol mismatch is reported as incommensurable
instead -- the same discipline model_arena.py already applies to model
protocols.
"""

from __future__ import annotations

from factorio_ai_lab.learning.survival import (
    RATE_PROTOCOL_OBSERVED_WINDOW,
    RATE_PROTOCOL_SLEEP_LITERAL,
    FitnessVector,
    compare_challenger,
)

CAPS = frozenset({"iron_backbone", "copper_mining"})


def _champion(protocol: str = RATE_PROTOCOL_SLEEP_LITERAL) -> FitnessVector:
    return FitnessVector(
        capabilities=CAPS,
        rates_per_s={"copper-ore": 0.8125, "iron-ore": 4.8571},
        measurement_protocol=protocol,
    )


def _challenger(protocol: str, copper: float = 0.25) -> FitnessVector:
    return FitnessVector(
        capabilities=CAPS,
        rates_per_s={"copper-ore": copper, "iron-ore": 1.0},
        measurement_protocol=protocol,
    )


def test_default_protocol_is_the_old_one() -> None:
    # Anything recorded before the field existed came from the sleep literal.
    assert FitnessVector().measurement_protocol == RATE_PROTOCOL_SLEEP_LITERAL
    restored = FitnessVector.from_dict({"capabilities": [], "rates_per_s": {}})
    assert restored.measurement_protocol == RATE_PROTOCOL_SLEEP_LITERAL


def test_protocol_survives_a_round_trip() -> None:
    vector = FitnessVector(measurement_protocol=RATE_PROTOCOL_OBSERVED_WINDOW)
    assert (
        FitnessVector.from_dict(vector.to_dict()).measurement_protocol
        == RATE_PROTOCOL_OBSERVED_WINDOW
    )


def test_mismatched_protocols_do_not_enforce_the_retention_floor() -> None:
    decision = compare_challenger(
        champion=_champion(RATE_PROTOCOL_SLEEP_LITERAL),
        challenger=_challenger(RATE_PROTOCOL_OBSERVED_WINDOW, copper=0.25),
    )
    assert not any("retention floor" in reason for reason in decision.regressions), (
        f"reprovou por piso de outro instrumento: {decision.regressions}"
    )


def test_mismatch_is_reported_rather_than_silently_skipped() -> None:
    decision = compare_challenger(
        champion=_champion(RATE_PROTOCOL_SLEEP_LITERAL),
        challenger=_challenger(RATE_PROTOCOL_OBSERVED_WINDOW),
    )
    reported = " ".join(decision.incommensurable_metrics)
    assert "rates_per_s" in reported
    assert RATE_PROTOCOL_SLEEP_LITERAL in reported
    assert RATE_PROTOCOL_OBSERVED_WINDOW in reported
    assert "rates_per_s" not in decision.compared_metrics


def test_matching_protocols_still_enforce_the_floor() -> None:
    # The guard must not become a way to dodge every rate comparison.
    decision = compare_challenger(
        champion=_champion(RATE_PROTOCOL_OBSERVED_WINDOW),
        challenger=_challenger(RATE_PROTOCOL_OBSERVED_WINDOW, copper=0.25),
    )
    assert any("copper-ore rate" in reason for reason in decision.regressions)
    assert "rates_per_s" in decision.compared_metrics


def test_matching_protocols_accept_a_challenger_that_holds_the_floor() -> None:
    decision = compare_challenger(
        champion=_champion(RATE_PROTOCOL_OBSERVED_WINDOW),
        challenger=FitnessVector(
            capabilities=CAPS,
            rates_per_s={"copper-ore": 0.9, "iron-ore": 5.0},
            measurement_protocol=RATE_PROTOCOL_OBSERVED_WINDOW,
        ),
    )
    assert not decision.regressions, decision.regressions
