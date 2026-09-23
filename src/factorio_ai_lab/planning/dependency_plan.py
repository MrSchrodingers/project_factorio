"""Differential dependency planning over the live Factorio catalog.

:class:`~factorio_ai_lab.planning.runtime_catalog.RuntimeFactorioCatalog`
already indexes every recipe the runtime reported and
:class:`~factorio_ai_lab.planning.production_dag.ProductionDagPlanner` already
turns a target into a rate balanced DAG that bottoms out in raw material. This
module does not repeat either. It consumes both and adds the four things a
runner needs before it can act on a target and that neither of them answers.

*What is missing, not what is needed.* The existing DAG states the absolute
chain. A plan is the difference between that chain and what the world and the
inventory already hold, in whole crafts, in an order that can be executed.

*Whether the recipe is available at all.* Every recipe row carries ``enabled``
and the technology table states what unlocks it. The existing expansion reads
neither, so it proposes a locked recipe as if it were craftable. A step here
carries :attr:`PlanStep.craftable_now` and, when it is false, the unresearched
technologies that stand in the way, prerequisites first.

*The machine is a requirement too.* A recipe needs a machine that reports its
crafting category, and that machine is an item with its own recipe. When the
world does not hold enough of them the plan expands the machine like any other
item. Two ledgers keep the two questions apart:
:attr:`DependencyPlan.machine_reservations` is what every step of the finished
plant needs, :attr:`DependencyPlan.machine_requirements` is what this plan
builds.

*Capacity, when a rate is asked for.* Machines per step follow from the recipe
time on the raw row and the crafting speed the machine prototype reported. Both
arrive with a status. A figure that does not carry ``measured`` yields
:data:`CAPACITY_INDETERMINATE` and no number, never a zero and never a default.

Three limits are stated rather than hidden.

* *Machine expansion is one level deep.* Machines missing from the world become
  steps and their ingredients are expanded, but the machines those steps would
  themselves need are not expanded again. Recursing there does not converge:
  an assembling machine is built from parts an assembling machine makes. The
  residue is declared as :data:`BLOCKER_MACHINE_BOOTSTRAP`, naming the item and
  the machine, which is the honest form of "this cannot be automated from
  nothing".
* *Cycles are cut, not raised.* ``ProductionDagPlanner`` raises ``ValueError``
  on a dependency cycle. The provider handed to it here makes the item that
  closes the cycle opaque, so the cycle leaves the plan as a declared blocker
  and an unresolved requirement instead of an exception.
* *One product per recipe.* ``recipe_choice`` picks a single product per item
  and discards the byproducts, so a plan for a target that is a byproduct of a
  multi-output recipe over-counts the other outputs. That belongs to the
  catalog, not here.

The module is pure: a catalog and mappings in, dataclasses out. No RCON and
no I/O.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factorio_ai_lab.planning.patterns import recipe_time
from factorio_ai_lab.planning.production_dag import (
    ProductionDagPlanner,
    RecipeSpec,
)
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_UNKNOWN,
    RuntimeFactorioCatalog,
    RuntimeRecipeChoice,
)

#: Items the planner stops at. Everything here comes out of the ground, out of
#: a tree or out of a pump, so no recipe can expand it further.
DEFAULT_RAW_MATERIALS = frozenset(
    {
        "iron-ore",
        "copper-ore",
        "coal",
        "stone",
        "water",
        "wood",
        "crude-oil",
    }
)

#: Machine prototypes that are the player rather than a placeable machine.
#: Excluded by default so a plan describes automation; pass an empty set and
#: name ``character`` in ``machine_preference`` to plan hand crafting.
PLAYER_MACHINE_TYPES = frozenset({"character"})

#: How deep the recipe walk goes before it declares the walk unfinished.
DEFAULT_MAX_DEPTH = 32

_EPSILON = 1e-9

#: No recipe in the catalog produces this item.
BLOCKER_RECIPE_ABSENT = "recipe_absent"
#: The recipe is disabled and no technology unlocks it.
BLOCKER_RECIPE_DISABLED = "recipe_disabled_without_unlock"
#: The recipe exists but the technology that unlocks it is not researched.
BLOCKER_TECHNOLOGY_LOCKED = "technology_locked"
#: A raw material the plan needs has no source among the declared ones.
BLOCKER_RAW_SOURCE_ABSENT = "raw_source_absent"
#: The recipe graph refers back to itself.
BLOCKER_RECIPE_CYCLE = "recipe_cycle"
#: The machine a step needs is built from what that step produces.
BLOCKER_MACHINE_BOOTSTRAP = "machine_bootstrap_cycle"
#: No machine prototype reports the crafting category the recipe needs.
BLOCKER_MACHINE_ABSENT = "no_machine_for_category"
#: The walk stopped at ``max_depth`` with ingredients still unexpanded.
BLOCKER_MAX_DEPTH = "max_depth_exceeded"

#: Machine count sized from a target rate with measured time and speed.
MACHINE_COUNT_DERIVED = "derived_from_measured"
#: Machine count is the one machine without which the step cannot run at all.
#: A lower bound, not a sizing.
MACHINE_COUNT_MINIMUM = "minimum_to_execute"

#: Capacity computed from figures that all carried ``measured``.
CAPACITY_DERIVED = "derived_from_measured"
#: At least one input carried no status that vouches for it, so the capacity
#: has no value. Distinct from zero on purpose.
CAPACITY_INDETERMINATE = "indeterminate"

#: What a capacity figure was missing.
MISSING_MACHINE = "machine"
MISSING_CRAFTING_TIME = "crafting_time"
MISSING_CRAFTING_SPEED = "crafting_speed"


def _name_sequence(raw: Any) -> tuple[str, ...]:
    """Names out of a field the runtime may serialise as a list or a table.

    An empty Lua table arrives as ``{}``, which json turns into a mapping, so
    ``prerequisites`` is a list on one row and a mapping on the next. Both are
    read; a mapping contributes its keys in sorted order.
    """
    if isinstance(raw, Mapping):
        return tuple(sorted(str(key) for key in raw))
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return tuple(str(entry) for entry in raw if entry)
    return ()


def _positive_counts(raw: Mapping[str, Any] | None) -> dict[str, float]:
    counts: dict[str, float] = {}
    if not isinstance(raw, Mapping):
        return counts
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        amount = float(value)
        if not math.isfinite(amount) or amount <= 0.0:
            continue
        counts[str(key)] = amount
    return counts


@dataclass(frozen=True)
class Blocker:
    """One reason the plan cannot be executed as written."""

    kind: str
    item: str
    detail: str
    machine: str | None = None
    missing_technologies: tuple[str, ...] = ()
    chain: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "item": self.item,
            "detail": self.detail,
            "machine": self.machine,
            "missing_technologies": list(self.missing_technologies),
            "chain": list(self.chain),
        }


@dataclass(frozen=True)
class PlanStep:
    """One recipe the plan has to run, and what qualifies it."""

    item: str
    recipe_name: str
    recipe_category: str
    required_count: float
    crafts: int
    output_count: float
    depth: int
    craftable_now: bool
    blocked_by_technologies: tuple[str, ...]
    machine: str | None
    machine_count: int
    machine_count_status: str
    crafting_time_s: float | None
    crafting_time_status: str
    is_machine_requirement: bool
    required_by: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "recipe_name": self.recipe_name,
            "recipe_category": self.recipe_category,
            "required_count": self.required_count,
            "crafts": self.crafts,
            "output_count": self.output_count,
            "depth": self.depth,
            "craftable_now": self.craftable_now,
            "blocked_by_technologies": list(self.blocked_by_technologies),
            "machine": self.machine,
            "machine_count": self.machine_count,
            "machine_count_status": self.machine_count_status,
            "crafting_time_s": self.crafting_time_s,
            "crafting_time_status": self.crafting_time_status,
            "is_machine_requirement": self.is_machine_requirement,
            "required_by": list(self.required_by),
        }


@dataclass(frozen=True)
class CapacityRequirement:
    """Machines one step needs to sustain a rate, with the chain behind it.

    ``machines`` is ``None`` whenever ``status`` is
    :data:`CAPACITY_INDETERMINATE`, so a capacity that could not be
    established can never be read as a capacity of zero.
    """

    item: str
    recipe_name: str
    rate_per_s: float
    crafts_per_s: float
    machine: str | None
    crafting_time_s: float | None
    crafting_time_status: str
    crafting_speed: float | None
    crafting_speed_status: str
    machines: int | None
    status: str
    missing: tuple[str, ...]

    @property
    def determined(self) -> bool:
        return self.machines is not None and self.status == CAPACITY_DERIVED

    def as_dict(self) -> dict[str, Any]:
        return {
            "item": self.item,
            "recipe_name": self.recipe_name,
            "rate_per_s": self.rate_per_s,
            "crafts_per_s": self.crafts_per_s,
            "machine": self.machine,
            "crafting_time_s": self.crafting_time_s,
            "crafting_time_status": self.crafting_time_status,
            "crafting_speed": self.crafting_speed,
            "crafting_speed_status": self.crafting_speed_status,
            "machines": self.machines,
            "status": self.status,
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class DependencyPlan:
    """What is missing for a target, in what order, and what stands in the way.

    An empty :attr:`steps` never means "nothing to do" on its own: either the
    inventory already covers the target, or :attr:`blockers` says why no step
    could be produced.
    """

    target_item: str
    target_count: float
    target_rate_per_s: float | None
    steps: tuple[PlanStep, ...]
    raw_requirements: Mapping[str, float]
    unresolved_requirements: Mapping[str, float]
    machine_reservations: Mapping[str, int]
    machine_requirements: Mapping[str, float]
    capacity: tuple[CapacityRequirement, ...]
    raw_rate_per_s: Mapping[str, float] | None
    blockers: tuple[Blocker, ...]
    missing_technologies: tuple[str, ...]
    raw_sources_declared: bool
    available: Mapping[str, float]

    @property
    def feasible(self) -> bool:
        """True when nothing at all stands in the way of executing the plan."""
        return not self.blockers

    def step(self, item: str) -> PlanStep | None:
        return next((step for step in self.steps if step.item == item), None)

    def capacity_for(self, item: str) -> CapacityRequirement | None:
        return next(
            (entry for entry in self.capacity if entry.item == item),
            None,
        )

    def blocker_kinds(self) -> frozenset[str]:
        return frozenset(blocker.kind for blocker in self.blockers)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_item": self.target_item,
            "target_count": self.target_count,
            "target_rate_per_s": self.target_rate_per_s,
            "feasible": self.feasible,
            "steps": [step.as_dict() for step in self.steps],
            "raw_requirements": dict(sorted(self.raw_requirements.items())),
            "unresolved_requirements": dict(
                sorted(self.unresolved_requirements.items())
            ),
            "machine_reservations": dict(
                sorted(self.machine_reservations.items())
            ),
            "machine_requirements": dict(
                sorted(self.machine_requirements.items())
            ),
            "capacity": [entry.as_dict() for entry in self.capacity],
            "raw_rate_per_s": (
                None
                if self.raw_rate_per_s is None
                else dict(sorted(self.raw_rate_per_s.items()))
            ),
            "blockers": [blocker.as_dict() for blocker in self.blockers],
            "missing_technologies": list(self.missing_technologies),
            "raw_sources_declared": self.raw_sources_declared,
            "available": dict(sorted(self.available.items())),
        }


@dataclass(frozen=True)
class _Availability:
    choice: RuntimeRecipeChoice | None
    craftable: bool
    missing_technologies: tuple[str, ...]
    blocker_kind: str | None


class _BlockerLog:
    """Blockers in insertion order, one per distinct fact."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str, str | None, tuple[str, ...]]] = set()
        self._entries: list[Blocker] = []

    def add(self, blocker: Blocker) -> None:
        key = (blocker.kind, blocker.item, blocker.machine, blocker.chain)
        if key in self._seen:
            return
        self._seen.add(key)
        self._entries.append(blocker)

    def entries(self) -> tuple[Blocker, ...]:
        return tuple(self._entries)


class DependencyPlanner:
    """Plans what is missing for a target against a runtime catalog."""

    def __init__(
        self,
        catalog: RuntimeFactorioCatalog,
        *,
        raw_materials: Iterable[str] = DEFAULT_RAW_MATERIALS,
        researched: Iterable[str] | None = None,
        machine_preference: Iterable[str] = (),
        excluded_machine_types: Iterable[str] = PLAYER_MACHINE_TYPES,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        self.catalog = catalog
        self.raw_materials = frozenset(str(name) for name in raw_materials)
        self.machine_preference = tuple(str(name) for name in machine_preference)
        self.excluded_machine_types = frozenset(
            str(name) for name in excluded_machine_types
        )
        self.max_depth = int(max_depth)
        self._technology_rows: dict[str, Mapping[str, Any]] = {
            str(row.get("name")): row
            for row in catalog.technology_rows
            if row.get("name")
        }
        if researched is None:
            self.researched = frozenset(
                name
                for name, row in self._technology_rows.items()
                if bool(row.get("researched"))
            )
        else:
            self.researched = frozenset(str(name) for name in researched)

    # -- catalog reads ----------------------------------------------------

    def availability(self, item: str) -> _Availability:
        """Whether ``item`` can be crafted now, and what stands in the way."""
        choice = self.catalog.recipe_choice(item)
        if choice is None:
            return _Availability(None, False, (), BLOCKER_RECIPE_ABSENT)
        if choice.enabled:
            return _Availability(choice, True, (), None)
        technologies = self.catalog.unlock_technologies(item)
        if not technologies:
            return _Availability(choice, False, (), BLOCKER_RECIPE_DISABLED)
        if any(name in self.researched for name in technologies):
            return _Availability(choice, True, (), None)
        chains = [self._unresearched_chain(name) for name in technologies]
        chains = [chain for chain in chains if chain]
        if not chains:
            return _Availability(choice, True, (), None)
        cheapest = min(chains, key=lambda chain: (len(chain), chain))
        return _Availability(choice, False, cheapest, BLOCKER_TECHNOLOGY_LOCKED)

    def _unresearched_chain(self, technology: str) -> tuple[str, ...]:
        """Unresearched technologies to research, prerequisites first."""
        ordered: list[str] = []
        done: set[str] = set()
        walking: set[str] = set()

        def visit(name: str) -> None:
            if name in done or name in walking:
                return
            if name in self.researched:
                done.add(name)
                return
            walking.add(name)
            row = self._technology_rows.get(name)
            if row is not None:
                for prerequisite in _name_sequence(row.get("prerequisites")):
                    visit(prerequisite)
            walking.discard(name)
            done.add(name)
            ordered.append(name)

        visit(technology)
        return tuple(ordered)

    def machine_for_category(
        self,
        category: str,
        available: Mapping[str, float] | None = None,
    ) -> str | None:
        """Machine that runs ``category``, or ``None`` when none reports it.

        Preference order, all of it deterministic: a name the caller asked
        for, then a machine the world already holds, then the machine with
        the lowest measured crafting speed, which is the lowest tier the
        runtime reported. A machine with no measured speed sorts last so a
        missing measurement never wins the choice.
        """
        on_hand = _positive_counts(available)
        candidates: list[str] = []
        for row in self.catalog.machine_rows:
            name = str(row.get("name") or "")
            if not name:
                continue
            if str(row.get("type") or "") in self.excluded_machine_types:
                continue
            if category not in _name_sequence(row.get("crafting_categories")):
                continue
            candidates.append(name)
        if not candidates:
            return None
        for preferred in self.machine_preference:
            if preferred in candidates:
                return preferred

        def sort_key(name: str) -> tuple[int, int, float, str]:
            speed = self.catalog.machine_speed(name).crafting_speed
            return (
                0 if on_hand.get(name, 0.0) > 0.0 else 1,
                1 if speed is None else 0,
                speed if speed is not None else 0.0,
                name,
            )

        return min(candidates, key=sort_key)

    # -- expansion --------------------------------------------------------

    def _provider(self, opaque: frozenset[str]):
        """``recipe_provider`` with the cycle and depth cuts applied."""
        catalog_provider = self.catalog.recipe_provider

        def provider(item: str) -> RecipeSpec | None:
            if item in opaque:
                return None
            return catalog_provider(item)

        return provider

    def _cuts(self, roots: Sequence[str], opaque: set[str]) -> tuple[
        list[tuple[str, ...]],
        list[str],
    ]:
        """Items to make opaque so the reused expansion terminates.

        Walks the ingredient graph with the same provider the DAG planner
        will use. An item that re-enters the walk stack closes a cycle and an
        item past ``max_depth`` closes the budget; both are made opaque, which
        turns them into declared unresolved requirements instead of an
        exception or an unbounded walk.
        """
        cycles: list[tuple[str, ...]] = []
        too_deep: list[str] = []
        stack: list[str] = []
        seen: set[str] = set()

        def visit(item: str) -> None:
            if item in self.raw_materials or item in opaque:
                return
            spec = self.catalog.recipe_provider(item)
            if spec is None:
                return
            if item in stack:
                start = stack.index(item)
                cycles.append(tuple(stack[start:]) + (item,))
                opaque.add(stack[-1])
                return
            if len(stack) >= self.max_depth:
                too_deep.append(item)
                opaque.add(item)
                return
            if item in seen:
                return
            seen.add(item)
            stack.append(item)
            for ingredient in spec.ingredients:
                visit(ingredient.item)
            stack.pop()

        for root in roots:
            visit(root)
        return cycles, too_deep

    def _merge_dag(
        self,
        root: str,
        opaque: frozenset[str],
        nodes: dict[str, RuntimeRecipeChoice],
        edges: dict[str, set[str]],
    ) -> None:
        """Merge the reused DAG for ``root`` into the shared node set."""
        planner = ProductionDagPlanner(
            self._provider(opaque),
            raw_items=self.raw_materials,
        )
        dag = planner.plan(root, 1.0)
        for node in dag.nodes:
            choice = self.catalog.recipe_choice(node.item)
            if choice is None:
                continue
            nodes[node.item] = choice
            edges.setdefault(node.item, set()).update(
                ingredient.item for ingredient in choice.spec.ingredients
            )

    # -- plan -------------------------------------------------------------

    def plan(
        self,
        target_item: str,
        count: float = 1.0,
        *,
        rate_per_s: float | None = None,
        available: Mapping[str, Any] | None = None,
        raw_sources: Iterable[str] | None = None,
    ) -> DependencyPlan:
        """What is still missing to obtain ``count`` of ``target_item``."""
        target_count = float(count)
        if not math.isfinite(target_count) or target_count <= 0.0:
            raise ValueError("count must be a positive finite number")
        if rate_per_s is not None:
            rate_per_s = float(rate_per_s)
            if not math.isfinite(rate_per_s) or rate_per_s <= 0.0:
                raise ValueError("rate_per_s must be a positive finite number")

        stock = _positive_counts(available)
        world = dict(stock)
        declared_sources = (
            None
            if raw_sources is None
            else frozenset(str(name) for name in raw_sources)
        )
        log = _BlockerLog()

        opaque: set[str] = set()
        cycles, too_deep = self._cuts([target_item], opaque)
        nodes: dict[str, RuntimeRecipeChoice] = {}
        edges: dict[str, set[str]] = {}
        self._merge_dag(target_item, frozenset(opaque), nodes, edges)

        capacity, raw_rate = self._capacity(target_item, rate_per_s, world, opaque)
        machine_counts = {
            entry.item: entry.machines
            for entry in capacity
            if entry.machines is not None
        }

        # Phase one: the target's own chain, against the inventory.
        demand_roots: dict[str, float] = {target_item: target_count}
        walk = self._walk(nodes, edges, demand_roots, dict(stock))
        reservations, machine_of = self._reserve(walk.steps, nodes, machine_counts, world)
        requirements = {
            machine: reserved - walk.stock.get(machine, 0.0)
            for machine, reserved in sorted(reservations.items())
            if reserved - walk.stock.get(machine, 0.0) > _EPSILON
        }

        # Phase two: machines the world cannot supply become steps of their
        # own. A machine that is already a step of phase one is part of its
        # own construction, so it is declared rather than expanded again.
        built = {
            machine: deficit
            for machine, deficit in requirements.items()
            if machine not in walk.steps
        }
        if built:
            for machine in sorted(built):
                more_cycles, more_deep = self._cuts([machine], opaque)
                cycles.extend(more_cycles)
                too_deep.extend(more_deep)
            frozen = frozenset(opaque)
            nodes = {}
            edges = {}
            for root in [target_item, *sorted(built)]:
                self._merge_dag(root, frozen, nodes, edges)
            demand_roots = {target_item: target_count, **built}
            walk = self._walk(
                nodes,
                edges,
                demand_roots,
                self._stock_after_reservation(stock, reservations, walk.stock),
            )
            reservations, machine_of = self._reserve(
                walk.steps,
                nodes,
                machine_counts,
                world,
            )

        for chain in cycles:
            log.add(
                Blocker(
                    kind=BLOCKER_RECIPE_CYCLE,
                    item=chain[0],
                    detail="recipe dependency cycle: " + " -> ".join(chain),
                    chain=chain,
                )
            )
        for item in too_deep:
            log.add(
                Blocker(
                    kind=BLOCKER_MAX_DEPTH,
                    item=item,
                    detail=(
                        f"ingredient walk stopped at depth {self.max_depth}; "
                        f"{item} was left unexpanded"
                    ),
                )
            )

        order, ordering_cuts = self._order(
            walk.steps,
            edges,
            machine_of,
            nodes,
            target_item,
        )
        depths = self._depths(edges, nodes)
        steps = self._steps(
            order,
            walk,
            nodes,
            machine_of,
            machine_counts,
            depths,
            built,
            log,
        )

        for item, machine in ordering_cuts:
            log.add(
                Blocker(
                    kind=BLOCKER_MACHINE_BOOTSTRAP,
                    item=item,
                    machine=machine,
                    detail=(
                        f"{item} needs {machine}, and {machine} is built from "
                        f"what {item} produces"
                    ),
                )
            )
        for item, amount in sorted(walk.unresolved.items()):
            log.add(
                Blocker(
                    kind=BLOCKER_RECIPE_ABSENT,
                    item=item,
                    detail=(
                        f"no recipe produces {item}; {amount:g} still required"
                    ),
                )
            )
        if declared_sources is not None:
            for item in sorted(walk.raw):
                if item in declared_sources:
                    continue
                log.add(
                    Blocker(
                        kind=BLOCKER_RAW_SOURCE_ABSENT,
                        item=item,
                        detail=f"no declared source for raw material {item}",
                    )
                )

        missing_technologies: list[str] = []
        for step in steps:
            for technology in step.blocked_by_technologies:
                if technology not in missing_technologies:
                    missing_technologies.append(technology)

        return DependencyPlan(
            target_item=target_item,
            target_count=target_count,
            target_rate_per_s=rate_per_s,
            steps=steps,
            raw_requirements=dict(sorted(walk.raw.items())),
            unresolved_requirements=dict(sorted(walk.unresolved.items())),
            machine_reservations=dict(sorted(reservations.items())),
            machine_requirements=dict(sorted(built.items())),
            capacity=capacity,
            raw_rate_per_s=raw_rate,
            blockers=log.entries(),
            missing_technologies=tuple(missing_technologies),
            raw_sources_declared=declared_sources is not None,
            available=dict(sorted(world.items())),
        )

    # -- plan internals ---------------------------------------------------

    def _walk(
        self,
        nodes: Mapping[str, RuntimeRecipeChoice],
        edges: Mapping[str, set[str]],
        demand_roots: Mapping[str, float],
        stock: dict[str, float],
    ) -> _Walk:
        """Net counts for every node, consumers before producers."""
        order = self._ingredient_order(edges, nodes)
        demand: defaultdict[str, float] = defaultdict(float)
        required_by: dict[str, set[str]] = defaultdict(set)
        for item, amount in demand_roots.items():
            demand[item] += amount
        steps: dict[str, tuple[float, int]] = {}
        raw: defaultdict[str, float] = defaultdict(float)
        unresolved: defaultdict[str, float] = defaultdict(float)

        for item in reversed(order):
            needed = demand.get(item, 0.0)
            if needed <= _EPSILON:
                continue
            taken = min(stock.get(item, 0.0), needed)
            if taken > 0.0:
                stock[item] = stock.get(item, 0.0) - taken
            remaining = needed - taken
            if remaining <= _EPSILON:
                continue
            choice = nodes[item]
            crafts = math.ceil(remaining / choice.product_amount - _EPSILON)
            surplus = crafts * choice.product_amount - remaining
            if surplus > _EPSILON:
                stock[item] = stock.get(item, 0.0) + surplus
            steps[item] = (remaining, crafts)
            for ingredient in choice.spec.ingredients:
                demand[ingredient.item] += crafts * ingredient.count
                required_by[ingredient.item].add(item)

        for item, amount in sorted(demand.items()):
            if item in nodes or amount <= _EPSILON:
                continue
            taken = min(stock.get(item, 0.0), amount)
            if taken > 0.0:
                stock[item] = stock.get(item, 0.0) - taken
            remaining = amount - taken
            if remaining <= _EPSILON:
                continue
            if item in self.raw_materials:
                raw[item] += remaining
            else:
                unresolved[item] += remaining

        return _Walk(
            steps=steps,
            raw=dict(raw),
            unresolved=dict(unresolved),
            required_by={key: frozenset(value) for key, value in required_by.items()},
            stock=stock,
        )

    @staticmethod
    def _stock_after_reservation(
        stock: Mapping[str, float],
        reservations: Mapping[str, int],
        leftover: Mapping[str, float],
    ) -> dict[str, float]:
        """Stock with the machines the first pass reserved taken out.

        A machine the world already holds pays for exactly one thing: the
        step that reserved it. Without this the same unit would also pay for
        its own deficit, and a plan one machine short would report the
        shortfall and then fail to plan the machine.
        """
        remaining = dict(stock)
        for machine, reserved in reservations.items():
            held = min(float(reserved), leftover.get(machine, 0.0))
            if held <= 0.0:
                continue
            remaining[machine] = max(0.0, remaining.get(machine, 0.0) - held)
        return remaining

    def _reserve(
        self,
        steps: Mapping[str, tuple[float, int]],
        nodes: Mapping[str, RuntimeRecipeChoice],
        machine_counts: Mapping[str, int],
        world: Mapping[str, float],
    ) -> tuple[dict[str, int], dict[str, str | None]]:
        reservations: defaultdict[str, int] = defaultdict(int)
        machine_of: dict[str, str | None] = {}
        for item in sorted(steps):
            category = nodes[item].spec.category
            machine = self.machine_for_category(category, world)
            machine_of[item] = machine
            if machine is None:
                continue
            reservations[machine] += max(1, int(machine_counts.get(item, 1)))
        return dict(reservations), machine_of

    def _order(
        self,
        steps: Mapping[str, tuple[float, int]],
        edges: Mapping[str, set[str]],
        machine_of: Mapping[str, str | None],
        nodes: Mapping[str, RuntimeRecipeChoice],
        target_item: str,
    ) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
        """Execution order over ingredient edges plus machine edges.

        A machine edge only exists when the plan has to build that machine.
        An edge that closes a cycle is dropped and reported, which is the
        bootstrap case: the machine is made of what the step produces.
        """
        full: dict[str, set[str]] = {
            item: set(dependencies) for item, dependencies in edges.items()
        }
        cuts: list[tuple[str, str]] = []
        for item in sorted(steps):
            machine = machine_of.get(item)
            if machine is None or machine not in steps:
                continue
            if _reaches(full, machine, item) or machine == item:
                cuts.append((item, machine))
                continue
            full.setdefault(item, set()).add(machine)

        order: list[str] = []
        done: set[str] = set()
        walking: set[str] = set()

        def visit(item: str) -> None:
            if item in done or item in walking or item not in nodes:
                return
            walking.add(item)
            for dependency in sorted(full.get(item, ())):
                visit(dependency)
            walking.discard(item)
            done.add(item)
            if item in steps:
                order.append(item)

        visit(target_item)
        for item in sorted(steps):
            visit(item)
        return tuple(order), tuple(cuts)

    def _ingredient_order(
        self,
        edges: Mapping[str, set[str]],
        nodes: Mapping[str, RuntimeRecipeChoice],
    ) -> list[str]:
        order: list[str] = []
        done: set[str] = set()
        walking: set[str] = set()

        def visit(item: str) -> None:
            if item in done or item in walking or item not in nodes:
                return
            walking.add(item)
            for dependency in sorted(edges.get(item, ())):
                visit(dependency)
            walking.discard(item)
            done.add(item)
            order.append(item)

        for item in sorted(nodes):
            visit(item)
        return order

    def _depths(
        self,
        edges: Mapping[str, set[str]],
        nodes: Mapping[str, RuntimeRecipeChoice],
    ) -> dict[str, int]:
        """Longest ingredient path from raw material, machines excluded."""
        depths: dict[str, int] = {}
        walking: set[str] = set()

        def depth_of(item: str) -> int:
            if item not in nodes:
                return 0
            if item in depths:
                return depths[item]
            if item in walking:
                return 0
            walking.add(item)
            below = [depth_of(dependency) for dependency in edges.get(item, ())]
            walking.discard(item)
            depths[item] = 1 + max(below, default=0)
            return depths[item]

        for item in nodes:
            depth_of(item)
        return depths

    def _steps(
        self,
        order: Sequence[str],
        walk: _Walk,
        nodes: Mapping[str, RuntimeRecipeChoice],
        machine_of: Mapping[str, str | None],
        machine_counts: Mapping[str, int],
        depths: Mapping[str, int],
        built: Mapping[str, float],
        log: _BlockerLog,
    ) -> tuple[PlanStep, ...]:
        steps: list[PlanStep] = []
        for item in order:
            required_count, crafts = walk.steps[item]
            choice = nodes[item]
            availability = self.availability(item)
            if availability.blocker_kind == BLOCKER_TECHNOLOGY_LOCKED:
                log.add(
                    Blocker(
                        kind=BLOCKER_TECHNOLOGY_LOCKED,
                        item=item,
                        detail=(
                            f"recipe {choice.recipe_name} is not unlocked; "
                            "research "
                            + ", ".join(availability.missing_technologies)
                        ),
                        missing_technologies=availability.missing_technologies,
                    )
                )
            elif availability.blocker_kind == BLOCKER_RECIPE_DISABLED:
                log.add(
                    Blocker(
                        kind=BLOCKER_RECIPE_DISABLED,
                        item=item,
                        detail=(
                            f"recipe {choice.recipe_name} is disabled and no "
                            "technology unlocks it"
                        ),
                    )
                )
            machine = machine_of.get(item)
            if machine is None:
                log.add(
                    Blocker(
                        kind=BLOCKER_MACHINE_ABSENT,
                        item=item,
                        detail=(
                            "no machine prototype reports crafting category "
                            f"{choice.spec.category!r}"
                        ),
                    )
                )
            sized = machine_counts.get(item)
            time_s, time_status = recipe_time(self.catalog, choice.recipe_name)
            steps.append(
                PlanStep(
                    item=item,
                    recipe_name=choice.recipe_name,
                    recipe_category=choice.spec.category,
                    required_count=required_count,
                    crafts=crafts,
                    output_count=choice.product_amount,
                    depth=depths.get(item, 0),
                    craftable_now=availability.craftable,
                    blocked_by_technologies=availability.missing_technologies,
                    machine=machine,
                    machine_count=1 if sized is None else max(1, sized),
                    machine_count_status=(
                        MACHINE_COUNT_MINIMUM if sized is None else MACHINE_COUNT_DERIVED
                    ),
                    crafting_time_s=time_s,
                    crafting_time_status=time_status,
                    is_machine_requirement=item in built,
                    required_by=tuple(sorted(walk.required_by.get(item, ()))),
                )
            )
        return tuple(steps)

    def _capacity(
        self,
        target_item: str,
        rate_per_s: float | None,
        world: Mapping[str, float],
        opaque: set[str],
    ) -> tuple[tuple[CapacityRequirement, ...], dict[str, float] | None]:
        """Machines per step for a sustained rate, reusing the rate balancer.

        Sizing assumes continuous supply, so the inventory does not enter it:
        a one-off stock cannot sustain a rate. The node rates come from
        :class:`ProductionDagPlanner`; only the two figures it fabricates,
        recipe time and machine speed, are read again from the source that
        carries a status.
        """
        if rate_per_s is None:
            return (), None
        planner = ProductionDagPlanner(
            self._provider(frozenset(opaque)),
            raw_items=self.raw_materials,
        )
        dag = planner.plan(target_item, rate_per_s)
        requirements: list[CapacityRequirement] = []
        for node in dag.nodes:
            choice = self.catalog.recipe_choice(node.item)
            if choice is None:
                continue
            machine = self.machine_for_category(choice.spec.category, world)
            time_s, time_status = recipe_time(self.catalog, choice.recipe_name)
            if machine is None:
                speed: float | None = None
                speed_status = PROBE_UNKNOWN
            else:
                reading = self.catalog.machine_speed(machine)
                speed = (
                    reading.crafting_speed
                    if reading.crafting_speed_measured
                    else None
                )
                speed_status = reading.crafting_speed_status
            missing: list[str] = []
            if machine is None:
                missing.append(MISSING_MACHINE)
            if time_s is None:
                missing.append(MISSING_CRAFTING_TIME)
            if speed is None or speed <= 0.0:
                missing.append(MISSING_CRAFTING_SPEED)
            if missing:
                machines: int | None = None
                status = CAPACITY_INDETERMINATE
            else:
                assert time_s is not None and speed is not None
                machines = max(
                    1,
                    math.ceil(node.crafts_per_s * time_s / speed - _EPSILON),
                )
                status = CAPACITY_DERIVED
            requirements.append(
                CapacityRequirement(
                    item=node.item,
                    recipe_name=choice.recipe_name,
                    rate_per_s=node.target_rate_per_s,
                    crafts_per_s=node.crafts_per_s,
                    machine=machine,
                    crafting_time_s=time_s,
                    crafting_time_status=time_status,
                    crafting_speed=speed,
                    crafting_speed_status=speed_status,
                    machines=machines,
                    status=status,
                    missing=tuple(missing),
                )
            )
        return tuple(requirements), dict(dag.raw_requirements_per_s)


@dataclass(frozen=True)
class _Walk:
    steps: Mapping[str, tuple[float, int]]
    raw: Mapping[str, float]
    unresolved: Mapping[str, float]
    required_by: Mapping[str, frozenset[str]]
    stock: dict[str, float]


def _reaches(edges: Mapping[str, set[str]], source: str, target: str) -> bool:
    """Whether ``target`` is reachable from ``source``.

    Node sets here are tens of items, so a breadth first walk per machine
    edge is cheaper than maintaining a transitive closure.
    """
    if source == target:
        return True
    seen: set[str] = {source}
    frontier = [source]
    while frontier:
        current = frontier.pop()
        for following in edges.get(current, ()):
            if following == target:
                return True
            if following in seen:
                continue
            seen.add(following)
            frontier.append(following)
    return False
