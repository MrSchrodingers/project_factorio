"""What a stage may take from the standing world when it inherits no supplies.

An heir starts inside the factory its ancestor was promoted for, but the
inventory it starts with is whatever that ancestor happened to be carrying.
Generation 38 inherited ``coal: 8`` and no container at all: stage 0 spent the
eight on the drill it adopted, and every one of the eight placement trials of
stage 1 died on the next line, ``insert_item(Prototype.Coal, trial_drill,
quantity=12)``, with ``No coal to insert from your inventory``. The script
assumed a starting kit that inheritance never promised.

The world is not empty, though. It holds containers with fuel in them and, as
a rule, a container or two that nothing is wired to. This module decides what
of that a stage may draw on, over the same factory graph the rest of the lab
reasons with, and under two constraints:

``never take what is producing``
    a container that some inserter draws from is feeding a machine, and a
    container that anything at all is wired to is part of a chain. Only a
    container that no material edge touches may be carried off, and fuel is
    drawn from the containers that feed nothing before the ones that do. An
    heir that dismantles the inherited factory to run its own trial reports
    the ancestor's collapse as its own regression.

``a refusal is a result``
    when the world holds no fuel and no spare container, the plan says so, in
    words, and the stage stops on that sentence. Letting the engine answer
    instead produces a message about inventories that names nothing about
    supply, which is what eleven restarts of the loop were spent reading.

Every draw carries its provenance -- which container, how much, and whether
that container was feeding anything -- because the ledger is the only way to
tell a generation that produced from one that consumed its inheritance.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from math import hypot
from typing import Any

from factorio_ai_lab.learning.factory_graph import MATERIAL_RELATIONS

#: Category `factory_graph` files containers under.
CONTAINER_CATEGORY = "buffer"

#: Why a container was judged recoverable, as written into the journal. A
#: salvage with no recorded reason cannot be told from a dismantling.
REASON_NO_MATERIAL_EDGE = "no_material_edge_touches_it"

#: Why a plan does not cover what the stage asked for. One string per cause,
#: kept apart: a world with no fuel at all and a world whose containers are
#: merely short are different readings and lead to different next actions.
REFUSAL_NO_FUEL_IN_WORLD = "no_container_holds_the_fuel"
REFUSAL_FUEL_SHORT = "containers_hold_less_fuel_than_the_step_needs"
REFUSAL_NO_SPARE_CONTAINER = "every_container_belongs_to_a_chain"


@dataclass(frozen=True)
class ContainerRole:
    """One container of the standing world, and how it is wired.

    The two directions are kept apart because they mean different things. A
    container something drops into is the end of a chain; a container
    something draws from is the supply of a machine, and emptying it stops
    that machine. Neither may be carried off, and only the second makes a
    fuel draw destructive.
    """

    node_id: str
    name: str
    position: tuple[float, float]
    fed_by_chain: bool = False
    supplies_chain: bool = False

    @property
    def unattached(self) -> bool:
        """True only when no material edge touches this container at all."""
        return not self.fed_by_chain and not self.supplies_chain

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "position": {"x": self.position[0], "y": self.position[1]},
            "fed_by_chain": self.fed_by_chain,
            "supplies_chain": self.supplies_chain,
        }


@dataclass(frozen=True)
class FuelSource:
    """A container holding fuel, as the world reported it."""

    position: tuple[float, float]
    available: int
    supplies_chain: bool = False


@dataclass(frozen=True)
class FuelDraw:
    """How much of one container's fuel a stage plans to take, and from where."""

    position: tuple[float, float]
    quantity: int
    available: int
    supplies_chain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "position": {"x": self.position[0], "y": self.position[1]},
            "quantity": self.quantity,
            "available": self.available,
            "supplies_chain": self.supplies_chain,
        }


@dataclass(frozen=True)
class ContainerSalvage:
    """The one container a stage plans to carry off, and why it may.

    ``holding`` is everything sitting inside it, not just the fuel: picking a
    container up takes its contents with it, so a salvage that does not report
    them hides an inherited stock arriving in this generation's inventory.
    """

    position: tuple[float, float]
    name: str
    holding: int = 0
    reason: str = REASON_NO_MATERIAL_EDGE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "position": {"x": self.position[0], "y": self.position[1]},
            "holding": self.holding,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SupplyPlan:
    """What a stage will draw from the world, and what it could not find."""

    fuel_needed: int = 0
    fuel_carried: int = 0
    fuel_draws: tuple[FuelDraw, ...] = ()
    container_needed: bool = False
    containers_carried: int = 0
    salvage: ContainerSalvage | None = None
    refusals: tuple[str, ...] = ()

    @property
    def fuel_planned(self) -> int:
        """Fuel this plan takes out of the world."""
        return sum(draw.quantity for draw in self.fuel_draws)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fuel_needed": self.fuel_needed,
            "fuel_carried": self.fuel_carried,
            "fuel_planned": self.fuel_planned,
            "fuel_draws": [draw.to_dict() for draw in self.fuel_draws],
            "container_needed": self.container_needed,
            "containers_carried": self.containers_carried,
            "salvage": None if self.salvage is None else self.salvage.to_dict(),
            "refusals": list(self.refusals),
        }


def container_roles(
    graph: Mapping[str, Any],
    *,
    names: Collection[str] | None = None,
) -> tuple[ContainerRole, ...]:
    """Every container of a built factory graph, with its wiring read off it.

    Wiring is decided over ``MATERIAL_RELATIONS`` and nothing else, which is
    the same set ``producers_reaching_processor`` counts over: a consumer that
    reasoned about chains with a look-alike set would answer a different
    question while reading like this one. Power and fluid edges are not
    material flow -- a chest beside a pole is still wired to nothing.

    Ordered by position so the same world always answers the same sequence.
    """
    wanted = frozenset(names) if names is not None else None
    roles: dict[str, tuple[str, tuple[float, float]]] = {}
    for node in graph.get("nodes", ()) or ():
        if not isinstance(node, Mapping):
            continue
        name = str(node.get("name", ""))
        if wanted is not None:
            if name not in wanted:
                continue
        elif node.get("category") != CONTAINER_CATEGORY:
            continue
        identifier = node.get("id")
        if not isinstance(identifier, str):
            continue
        try:
            position = (float(node["x"]), float(node["y"]))
        except (KeyError, TypeError, ValueError):
            continue
        roles[identifier] = (name, position)

    fed: set[str] = set()
    supplies: set[str] = set()
    for edge in graph.get("edges", ()) or ():
        if not isinstance(edge, Mapping):
            continue
        if edge.get("relation") not in MATERIAL_RELATIONS:
            continue
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and source in roles:
            supplies.add(source)
        if isinstance(target, str) and target in roles:
            fed.add(target)

    return tuple(
        sorted(
            (
                ContainerRole(
                    node_id=identifier,
                    name=name,
                    position=position,
                    fed_by_chain=identifier in fed,
                    supplies_chain=identifier in supplies,
                )
                for identifier, (name, position) in roles.items()
            ),
            key=lambda role: (role.position[1], role.position[0], role.node_id),
        )
    )


def unattached_containers(
    graph: Mapping[str, Any],
    *,
    names: Collection[str] | None = None,
) -> tuple[ContainerRole, ...]:
    """Containers no material edge touches, in either direction.

    These are the only ones a stage may carry off. A container an inserter
    drops into is the output of a chain and a container an inserter draws from
    is the input of a machine; taking either one down breaks a factory this
    generation is supposed to be preserving, and the breakage would then be
    read as this genome's regression.
    """
    return tuple(role for role in container_roles(graph, names=names) if role.unattached)


def _distance_from(anchor: tuple[float, float], position: tuple[float, float]) -> float:
    return hypot(position[0] - anchor[0], position[1] - anchor[1])


def plan_supply(
    *,
    anchor: tuple[float, float],
    fuel_needed: int,
    fuel_carried: int = 0,
    fuel_sources: Sequence[FuelSource] = (),
    container_needed: bool = False,
    containers_carried: int = 0,
    spare_containers: Sequence[ContainerSalvage] = (),
) -> SupplyPlan:
    """Decide what the stage draws, from what the world was measured to hold.

    Only the shortfall is drawn: a stage that already carries what it needs
    takes nothing, so a generation is never charged for a withdrawal it did
    not make. Sources are spent in the order ``(supplies a chain, distance,
    position)`` -- a container that feeds nothing is emptied before one whose
    contents a machine is waiting for, and among equals the nearest wins, so
    the draw costs the least game time.

    At most one container is ever salvaged, the emptiest and then the nearest,
    because an empty one carries no inherited stock into this generation's
    inventory. Anything the plan cannot cover becomes a named refusal rather
    than a silent shortfall.
    """
    needed = max(0, int(fuel_needed))
    carried = max(0, int(fuel_carried))
    shortfall = max(0, needed - carried)
    draws: list[FuelDraw] = []
    refusals: list[str] = []

    if shortfall > 0:
        usable = [source for source in fuel_sources if source.available > 0]
        if not usable:
            refusals.append(REFUSAL_NO_FUEL_IN_WORLD)
        else:
            remaining = shortfall
            for source in sorted(
                usable,
                key=lambda item: (
                    item.supplies_chain,
                    _distance_from(anchor, item.position),
                    item.position[1],
                    item.position[0],
                ),
            ):
                if remaining <= 0:
                    break
                quantity = min(remaining, int(source.available))
                draws.append(
                    FuelDraw(
                        position=source.position,
                        quantity=quantity,
                        available=int(source.available),
                        supplies_chain=source.supplies_chain,
                    )
                )
                remaining -= quantity
            if remaining > 0:
                refusals.append(REFUSAL_FUEL_SHORT)

    salvage: ContainerSalvage | None = None
    if container_needed and int(containers_carried) < 1:
        if not spare_containers:
            refusals.append(REFUSAL_NO_SPARE_CONTAINER)
        else:
            salvage = min(
                spare_containers,
                key=lambda item: (
                    item.holding,
                    _distance_from(anchor, item.position),
                    item.position[1],
                    item.position[0],
                ),
            )

    return SupplyPlan(
        fuel_needed=needed,
        fuel_carried=carried,
        fuel_draws=tuple(draws),
        container_needed=bool(container_needed),
        containers_carried=max(0, int(containers_carried)),
        salvage=salvage,
        refusals=tuple(refusals),
    )
