"""Inheriting a factory must not read as having built one.

Persistence between generations is what gives selection something to act on,
but it introduces a specific way to lie: an heir starts holding capabilities
it did not earn. Crediting those would make every heir look like a
breakthrough on its first generation, and the system would report learning
where it only reported inheritance.

Losing an inherited capability is still a regression, because an heir that
destroys what it received is genuinely worse than one that keeps it.
"""

from __future__ import annotations

from factorio_ai_lab.learning.survival import FitnessVector, compare_challenger

BASE = frozenset({"iron_backbone"})


def _champion() -> FitnessVector:
    return FitnessVector(capabilities=BASE, rates_per_s={"iron-ore": 1.0})


def _challenger(
    capabilities: frozenset[str],
    inherited: frozenset[str] | None,
) -> FitnessVector:
    return FitnessVector(
        capabilities=capabilities,
        inherited_capabilities=inherited,
        rates_per_s={"iron-ore": 1.0},
    )


def test_without_inheritance_a_new_capability_is_credited() -> None:
    decision = compare_challenger(
        champion=_champion(),
        challenger=_challenger(BASE | {"copper_mining"}, inherited=None),
    )
    assert any("new capabilities" in item for item in decision.improvements)


def test_an_inherited_capability_is_not_credited() -> None:
    decision = compare_challenger(
        champion=_champion(),
        challenger=_challenger(
            BASE | {"copper_mining"},
            inherited=frozenset({"copper_mining"}),
        ),
    )
    assert not any("new capabilities" in item for item in decision.improvements), (
        f"creditou capacidade herdada: {decision.improvements}"
    )


def test_withholding_is_reported_not_silent() -> None:
    decision = compare_challenger(
        champion=_champion(),
        challenger=_challenger(
            BASE | {"copper_mining"},
            inherited=frozenset({"copper_mining"}),
        ),
    )
    reported = " ".join(decision.incommensurable_metrics)
    assert "inherited_capabilities" in reported
    assert "copper_mining" in reported


def test_a_capability_built_on_top_of_inheritance_is_credited() -> None:
    # The heir inherited copper and then built science on its own: only the
    # second is an achievement of this genome.
    decision = compare_challenger(
        champion=_champion(),
        challenger=_challenger(
            BASE | {"copper_mining", "automation_science"},
            inherited=frozenset({"copper_mining"}),
        ),
    )
    credited = [i for i in decision.improvements if "new capabilities" in i]
    assert credited, "nao creditou o que o genoma de fato construiu"
    assert "automation_science" in credited[0]
    assert "copper_mining" not in credited[0]


def test_losing_an_inherited_capability_is_still_a_regression() -> None:
    champion = FitnessVector(
        capabilities=BASE | {"copper_mining"},
        rates_per_s={"iron-ore": 1.0},
    )
    decision = compare_challenger(
        champion=champion,
        challenger=_challenger(BASE, inherited=frozenset({"copper_mining"})),
    )
    assert any("lost capabilities" in item for item in decision.regressions)


def test_empty_inheritance_differs_from_no_inheritance() -> None:
    # frozenset() means "inherited nothing"; None means "no inheritance in
    # play". Both must credit a genuinely new capability.
    for inherited in (None, frozenset()):
        decision = compare_challenger(
            champion=_champion(),
            challenger=_challenger(BASE | {"copper_mining"}, inherited=inherited),
        )
        assert any("new capabilities" in i for i in decision.improvements), inherited


def test_inheritance_survives_a_round_trip() -> None:
    vector = FitnessVector(
        capabilities=BASE,
        inherited_capabilities=frozenset({"iron_backbone"}),
    )
    restored = FitnessVector.from_dict(vector.to_dict())
    assert restored.inherited_capabilities == frozenset({"iron_backbone"})
    # A fitness recorded before the field existed has no inheritance in play.
    assert FitnessVector.from_dict({"capabilities": []}).inherited_capabilities is None
