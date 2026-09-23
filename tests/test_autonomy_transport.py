"""Autonomy must count the same transport the graph counts.

`factory_graph` learned that a splitter and an underground belt carry
material; `autonomy` kept a private copy of the belt names with only the
three plain tiers. Two modules disagreeing about what a conveyor is means a
factory distributing through a bus reads as having no distribution at all,
and `belt_count` -- which feeds the closed-loop gates -- understates it.

A second private copy of an inference rule drifts silently. This makes the
one that already exists in factory_graph the only one.
"""

from __future__ import annotations

from factorio_ai_lab.learning.autonomy import evaluate_factory_autonomy


def _entity(name, x, y, status="working"):
    return {"name": name, "position": {"x": x, "y": y}, "status": status}


def _world(transport_name):
    # A furnace fed by a drill, with transport between them, plus the
    # inserters and power the gates look for.
    return [
        _entity("burner-mining-drill", 0.0, 0.0),
        _entity(transport_name, 1.0, 0.0),
        _entity(transport_name, 2.0, 0.0),
        _entity("stone-furnace", 3.0, 0.0),
        _entity("burner-inserter", 1.0, 1.0),
        _entity("boiler", 5.0, 0.0),
        _entity("steam-engine", 7.0, 0.0),
    ]


def _belt_count(transport_name):
    evidence = evaluate_factory_autonomy(
        entities=_world(transport_name),
        interventions={},
        soak_runtime_s=100.0,
    )
    return evidence.to_dict()["belt_count"]


def test_a_plain_belt_is_counted() -> None:
    assert _belt_count("transport-belt") == 2


def test_an_underground_belt_is_counted_as_transport() -> None:
    assert _belt_count("underground-belt") == 2, (
        "a autonomia nao conta underground como transporte"
    )


def test_a_splitter_is_counted_as_transport() -> None:
    assert _belt_count("splitter") == 2, (
        "a autonomia nao conta splitter como transporte"
    )


def test_the_two_modules_agree_on_what_transport_is() -> None:
    # The drift this guards against is silent: autonomy would keep answering
    # a number, just a smaller one.
    from factorio_ai_lab.learning import autonomy
    from factorio_ai_lab.learning.factory_graph import TRANSPORT_NAMES

    assert autonomy._BELTS == TRANSPORT_NAMES, (
        "autonomy mantem uma copia divergente do vocabulario de transporte"
    )


def test_a_non_transport_entity_is_not_counted() -> None:
    assert _belt_count("wooden-chest") == 0
