"""Pure structural planning for buffered producer output.

F2-C answers the replicated F1 counterexample without introducing a
green-science-specific handler.  It takes a typed structural ActionRequest,
the observed factory graph/world and deterministic runtime catalog, then
produces processing branches in SHADOW.

No FLE call occurs here.  No TransactionalFLEExecutor is invoked.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionCondition,
    ActionFamily,
    ActionProvenance,
    ActionRequest,
    ConditionOperator,
    ConditionState,
    EvidenceRef,
    Refusal,
)
from factorio_ai_lab.evidence import EvidenceStatus
from factorio_ai_lab.learning.factory_graph import MATERIAL_RELATIONS, node_id
from factorio_ai_lab.learning.repair_loop import INTENT_PLACE_PROCESSING
from factorio_ai_lab.planning.delivery import (
    DEFAULT_BELT_BUDGET,
    DeliveryLink,
    plan_delivery,
)
from factorio_ai_lab.planning.dependency_plan import DependencyPlan, DependencyPlanner
from factorio_ai_lab.planning.footprints import blocked_tiles, entity_name, entity_tiles
from factorio_ai_lab.planning.placement import (
    OUTCOME_REFUSE,
    PlacementPlan,
    ResourceSurvey,
    plan_placement,
    scan_offsets,
)
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog

REFUSAL_WRONG_ACTION = "structural_wrong_action"
REFUSAL_NO_TARGETS = "structural_no_targets"
REFUSAL_TARGET_NOT_PRODUCER = "structural_target_not_producer"
REFUSAL_NO_BUFFER_PATH = "structural_no_buffer_path"
REFUSAL_BUFFER_ENTITY_UNREADABLE = "structural_buffer_entity_unreadable"
REFUSAL_BUFFER_CONTENTS_UNOBSERVED = "structural_buffer_contents_unobserved"
REFUSAL_BUFFER_EMPTY = "structural_buffer_empty"
REFUSAL_BUFFER_MATERIAL_AMBIGUOUS = "structural_buffer_material_ambiguous"
REFUSAL_MINING_MATERIAL_AMBIGUOUS = "structural_mining_material_ambiguous"
REFUSAL_NO_DIRECT_PROCESSING_RECIPE = "structural_no_direct_processing_recipe"
REFUSAL_NO_PROCESSOR_MACHINE = "structural_no_processor_machine"
REFUSAL_NO_PLACEMENT_DELIVERY = "structural_no_placement_delivery"

STATUS_READY = "ready"
STATUS_PARTIAL = "partial"
STATUS_REFUSED = "refused"


@dataclass(frozen=True)
class DirectProcessingRecipe:
    material: str
    recipe_name: str
    category: str
    products: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "material": self.material,
            "recipe_name": self.recipe_name,
            "category": self.category,
            "products": list(self.products),
        }


@dataclass(frozen=True)
class BufferedSource:
    material: str
    producer_id: str
    buffer_id: str
    observed_count: float | None
    evidence: EvidenceRef
    identity_basis: str = "buffer_contents"
    buffer_evidence: EvidenceRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "material": self.material,
            "producer_id": self.producer_id,
            "buffer_id": self.buffer_id,
            "observed_count": self.observed_count,
            "identity_basis": self.identity_basis,
            "evidence": self.evidence.to_dict(),
            "buffer_evidence": (
                None
                if self.buffer_evidence is None
                else self.buffer_evidence.to_dict()
            ),
        }


@dataclass(frozen=True)
class ProcessingBranch:
    """One material-specific structural option."""

    material: str
    product: str
    recipe: DirectProcessingRecipe
    processor: str
    producers: tuple[str, ...]
    buffers: tuple[str, ...]
    source_buffer: str
    machine_dependency: DependencyPlan
    placement: PlacementPlan
    delivery: DeliveryLink
    request: ActionRequest
    preconditions: tuple[ActionCondition, ...]
    postconditions: tuple[ActionCondition, ...]

    @property
    def executable_preconditions_satisfied(self) -> bool:
        return all(
            (not condition.hard) or condition.satisfied
            for condition in self.preconditions
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "material": self.material,
            "product": self.product,
            "recipe": self.recipe.to_dict(),
            "processor": self.processor,
            "producers": list(self.producers),
            "buffers": list(self.buffers),
            "source_buffer": self.source_buffer,
            "machine_dependency": self.machine_dependency.as_dict(),
            "placement": self.placement.to_dict(),
            "delivery": self.delivery.to_dict(),
            "request": self.request.to_dict(),
            "preconditions": [condition.to_dict() for condition in self.preconditions],
            "postconditions": [condition.to_dict() for condition in self.postconditions],
            "executable_preconditions_satisfied": self.executable_preconditions_satisfied,
        }


@dataclass(frozen=True)
class StructuralProcessingPlan:
    """All material branches derived from one structural repair request."""

    parent_request: ActionRequest
    sources: tuple[BufferedSource, ...] = ()
    branches: tuple[ProcessingBranch, ...] = ()
    refusals: tuple[Refusal, ...] = ()
    status: str = STATUS_REFUSED

    @property
    def ready(self) -> bool:
        return bool(self.branches) and all(
            branch.executable_preconditions_satisfied for branch in self.branches
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ready": self.ready,
            "parent_request": self.parent_request.to_dict(),
            "sources": [source.to_dict() for source in self.sources],
            "branches": [branch.to_dict() for branch in self.branches],
            "refusals": [refusal.to_dict() for refusal in self.refusals],
        }


def _positive_contents(entity: Mapping[str, Any]) -> dict[str, float] | None:
    """Observed positive contents, or None when the field was never measured."""

    if "contents" not in entity:
        return None
    raw = entity.get("contents")
    counts: dict[str, float] = {}
    if isinstance(raw, Mapping):
        iterator = (
            {"name": name, "count": count}
            for name, count in raw.items()
        )
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        iterator = (row for row in raw if isinstance(row, Mapping))
    else:
        return None
    for row in iterator:
        name = str(row.get("name") or "")
        count = row.get("count")
        if (
            name
            and isinstance(count, (int, float))
            and not isinstance(count, bool)
            and float(count) > 0
        ):
            counts[name] = counts.get(name, 0.0) + float(count)
    return counts


def _graph_indexes(
    graph: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, set[str]]]:
    nodes = {
        str(row.get("id")): row
        for row in graph.get("nodes", [])
        if isinstance(row, Mapping) and row.get("id")
    }
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("edges", []):
        if not isinstance(edge, Mapping):
            continue
        if str(edge.get("relation")) not in MATERIAL_RELATIONS:
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target:
            adjacency[source].add(target)
    return nodes, adjacency


def _reachable_buffers(
    producer_id: str,
    *,
    nodes: Mapping[str, Mapping[str, Any]],
    adjacency: Mapping[str, set[str]],
) -> tuple[str, ...]:
    """Nearest downstream buffer frontier for one producer.

    A buffer is a causal observation boundary: once material reaches it, later
    shared belts/buffers no longer identify what this producer itself emitted.
    The BFS therefore returns only buffers at the minimum material-path
    distance. Multiple equally-near buffers remain visible so conflicting
    contents are refused as genuinely ambiguous.
    """

    frontier = {producer_id}
    seen = {producer_id}
    while frontier:
        next_frontier: set[str] = set()
        buffers: set[str] = set()
        for current in sorted(frontier):
            for target in sorted(adjacency.get(current, ())):
                if target in seen:
                    continue
                seen.add(target)
                row = nodes.get(target)
                if row is None:
                    continue
                if str(row.get("category")) == "buffer":
                    buffers.add(target)
                else:
                    next_frontier.add(target)
        if buffers:
            return tuple(sorted(buffers))
        frontier = next_frontier
    return ()


def _world_index(
    entities: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    return {
        node_id(entity, index): entity
        for index, entity in enumerate(entities)
        if isinstance(entity, Mapping)
    }


def _mining_material_identity(
    producer_id: str,
    *,
    world: Mapping[str, Mapping[str, Any]],
    resources: ResourceSurvey | None,
    footprints: Mapping[str, tuple[int, int]] | None,
) -> tuple[str | None, EvidenceRef | None, Refusal | None]:
    """Material causally under one producer, when the ground was surveyed.

    Buffer contents are a downstream observation and may be contaminated by
    bootstrap fuel or another line. A mining drill standing over exactly one
    observed resource provides a stronger causal identity for its output.
    Multiple resource kinds under the producer remain ambiguous; no majority
    heuristic is allowed.
    """

    if resources is None:
        return None, None, None
    entity = world.get(producer_id)
    if entity is None:
        return None, None, None
    tiles = entity_tiles(entity, footprints)
    if not tiles:
        return None, None, None
    names = tuple(
        sorted(
            {
                resources.tiles[tile]
                for tile in tiles
                if tile in resources.tiles
            }
        )
    )
    if len(names) > 1:
        return (
            None,
            None,
            Refusal(
                code=REFUSAL_MINING_MATERIAL_AMBIGUOUS,
                detail=(
                    f"producer {producer_id} overlaps multiple observed resources: "
                    + ", ".join(names)
                ),
            ),
        )
    if not names:
        return None, None, None
    material = names[0]
    evidence = EvidenceRef(
        source="resource_survey",
        path=f"{producer_id}.footprint.{material}",
        status=EvidenceStatus.OBSERVED,
    )
    return material, evidence, None


def _infer_sources(
    request: ActionRequest,
    *,
    graph: Mapping[str, Any],
    world_entities: Sequence[Mapping[str, Any]],
    resources: ResourceSurvey | None = None,
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[tuple[BufferedSource, ...], tuple[Refusal, ...]]:
    nodes, adjacency = _graph_indexes(graph)
    world = _world_index(world_entities)
    sources: list[BufferedSource] = []
    refusals: list[Refusal] = []

    for producer_id in request.targets:
        node = nodes.get(producer_id)
        if node is None or str(node.get("category")) != "extraction":
            refusals.append(
                Refusal(
                    code=REFUSAL_TARGET_NOT_PRODUCER,
                    detail=f"{producer_id} is not an observed extraction producer",
                )
            )
            continue

        buffer_ids = _reachable_buffers(
            producer_id,
            nodes=nodes,
            adjacency=adjacency,
        )
        if not buffer_ids:
            refusals.append(
                Refusal(
                    code=REFUSAL_NO_BUFFER_PATH,
                    detail=f"{producer_id} reaches no observed buffer",
                    retriable=True,
                )
            )
            continue

        observed: dict[str, tuple[float, str]] = {}
        saw_unobserved = False
        for buffer_id in buffer_ids:
            entity = world.get(buffer_id)
            if entity is None:
                refusals.append(
                    Refusal(
                        code=REFUSAL_BUFFER_ENTITY_UNREADABLE,
                        detail=f"graph buffer {buffer_id} has no raw entity observation",
                        retriable=True,
                    )
                )
                continue
            contents = _positive_contents(entity)
            if contents is None:
                saw_unobserved = True
                continue
            for material, count in contents.items():
                previous = observed.get(material)
                if previous is None or count > previous[0]:
                    observed[material] = (count, buffer_id)

        mined_material, mining_evidence, mining_refusal = _mining_material_identity(
            producer_id,
            world=world,
            resources=resources,
            footprints=footprints,
        )
        if mining_refusal is not None:
            refusals.append(mining_refusal)
            continue

        if mined_material is not None and mining_evidence is not None:
            measured = observed.get(mined_material)
            if measured is None:
                buffer_id = buffer_ids[0]
                count = None
                buffer_evidence = None
            else:
                count, buffer_id = measured
                buffer_evidence = EvidenceRef(
                    source="world.entities",
                    path=f"{buffer_id}.contents.{mined_material}",
                    status=EvidenceStatus.OBSERVED,
                )
            sources.append(
                BufferedSource(
                    material=mined_material,
                    producer_id=producer_id,
                    buffer_id=buffer_id,
                    observed_count=count,
                    evidence=mining_evidence,
                    identity_basis="mining_resource",
                    buffer_evidence=buffer_evidence,
                )
            )
            continue

        if not observed:
            code = (
                REFUSAL_BUFFER_CONTENTS_UNOBSERVED
                if saw_unobserved
                else REFUSAL_BUFFER_EMPTY
            )
            refusals.append(
                Refusal(
                    code=code,
                    detail=f"no positive observed material for producer {producer_id}",
                    retriable=True,
                )
            )
            continue

        if len(observed) != 1:
            refusals.append(
                Refusal(
                    code=REFUSAL_BUFFER_MATERIAL_AMBIGUOUS,
                    detail=(
                        f"producer {producer_id} reaches buffers with multiple materials: "
                        + ", ".join(sorted(observed))
                    ),
                )
            )
            continue

        material, (count, buffer_id) = next(iter(observed.items()))
        evidence = EvidenceRef(
            source="world.entities",
            path=f"{buffer_id}.contents.{material}",
            status=EvidenceStatus.OBSERVED,
        )
        sources.append(
            BufferedSource(
                material=material,
                producer_id=producer_id,
                buffer_id=buffer_id,
                observed_count=count,
                evidence=evidence,
                identity_basis="buffer_contents",
                buffer_evidence=evidence,
            )
        )

    return tuple(sources), tuple(refusals)


def _direct_recipe(
    catalog: RuntimeFactorioCatalog,
    material: str,
) -> DirectProcessingRecipe | None:
    candidates: list[tuple[tuple[int, int, str], DirectProcessingRecipe]] = []
    for row in catalog.recipe_rows:
        if not bool(row.get("enabled")):
            continue
        raw_ingredients = row.get("ingredients")
        raw_products = row.get("products")
        if (
            not isinstance(raw_ingredients, Sequence)
            or isinstance(raw_ingredients, (str, bytes))
            or not isinstance(raw_products, Sequence)
            or isinstance(raw_products, (str, bytes))
        ):
            continue
        ingredients = [
            entry
            for entry in raw_ingredients
            if isinstance(entry, Mapping) and entry.get("name")
        ]
        names = {str(entry.get("name")) for entry in ingredients}
        if names != {material}:
            continue
        products = tuple(
            sorted(
                str(entry.get("name"))
                for entry in raw_products
                if isinstance(entry, Mapping) and entry.get("name")
            )
        )
        if not products:
            continue
        categories = row.get("categories")
        category = (
            str(categories[0])
            if isinstance(categories, Sequence)
            and not isinstance(categories, (str, bytes))
            and categories
            else "crafting"
        )
        recipe_name = str(row.get("name") or "")
        if not recipe_name:
            continue
        candidate = DirectProcessingRecipe(
            material=material,
            recipe_name=recipe_name,
            category=category,
            products=products,
        )
        candidates.append(
            (
                (
                    len(products),
                    len(ingredients),
                    recipe_name,
                ),
                candidate,
            )
        )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _child_provenance(
    parent: ActionRequest,
) -> ActionProvenance:
    p = parent.provenance
    return ActionProvenance(
        requested_by=p.requested_by,
        source_component="factorio_ai_lab.cortex.structural",
        code_revision=p.code_revision,
        run_id=p.run_id,
        generation=p.generation,
        parent_action_id=parent.action_id,
        policy_version=p.policy_version,
    )


def _best_placement_delivery(
    *,
    processor: str,
    source_entity: Mapping[str, Any],
    world_entities: Sequence[Mapping[str, Any]],
    footprints: Mapping[str, tuple[int, int]] | None,
    reach: int,
    belt_budget: int,
) -> tuple[PlacementPlan, DeliveryLink] | None:
    position = source_entity.get("position")
    if not isinstance(position, Mapping):
        return None
    try:
        source_position = (float(position["x"]), float(position["y"]))
    except (KeyError, TypeError, ValueError):
        return None

    source_tiles = entity_tiles(source_entity, footprints)
    if not source_tiles:
        return None
    occupied = blocked_tiles(world_entities, footprints)

    best: tuple[tuple[int, int, int, float, float], PlacementPlan, DeliveryLink] | None = None
    for dx, dy in scan_offsets(reach):
        anchor = (source_position[0] + dx, source_position[1] + dy)
        placement = plan_placement(
            entity=processor,
            anchor=anchor,
            world=world_entities,
            footprints=footprints,
            reach=0,
        )
        if placement.outcome == OUTCOME_REFUSE or not placement.tiles:
            continue
        delivery = plan_delivery(
            source_tiles=source_tiles,
            target_tiles=placement.tiles,
            blocked=occupied,
            belt_budget=belt_budget,
        )
        if not delivery.builds:
            continue
        key = (
            delivery.belt_count,
            abs(dx) + abs(dy),
            max(abs(dx), abs(dy)),
            placement.position[1] if placement.position is not None else 0.0,
            placement.position[0] if placement.position is not None else 0.0,
        )
        if best is None or key < best[0]:
            best = (key, placement, delivery)
    if best is None:
        return None
    return best[1], best[2]


def plan_processing_for_buffered_output(
    request: ActionRequest,
    *,
    graph: Mapping[str, Any],
    world_entities: Sequence[Mapping[str, Any]],
    catalog: RuntimeFactorioCatalog,
    available: Mapping[str, Any] | None = None,
    footprints: Mapping[str, tuple[int, int]] | None = None,
    resources: ResourceSurvey | None = None,
    placement_reach: int = 8,
    belt_budget: int = DEFAULT_BELT_BUDGET,
) -> StructuralProcessingPlan:
    """Build material-specific processing options without mutating Factorio."""

    if (
        request.family is not ActionFamily.PLACEMENT
        or request.intent != INTENT_PLACE_PROCESSING
    ):
        return StructuralProcessingPlan(
            parent_request=request,
            refusals=(
                Refusal(
                    code=REFUSAL_WRONG_ACTION,
                    detail=(
                        "structural planner only accepts "
                        f"placement:{INTENT_PLACE_PROCESSING}"
                    ),
                ),
            ),
        )
    if not request.targets:
        return StructuralProcessingPlan(
            parent_request=request,
            refusals=(
                Refusal(
                    code=REFUSAL_NO_TARGETS,
                    detail="structural processing request names no producer",
                ),
            ),
        )

    sources, source_refusals = _infer_sources(
        request,
        graph=graph,
        world_entities=world_entities,
        resources=resources,
        footprints=footprints,
    )
    groups: dict[str, list[BufferedSource]] = defaultdict(list)
    for source in sources:
        groups[source.material].append(source)

    world_index = _world_index(world_entities)
    branches: list[ProcessingBranch] = []
    refusals = list(source_refusals)
    stock = dict(available or {})
    if any(
        isinstance(entity, Mapping) and entity_name(entity) == "character"
        for entity in world_entities
    ):
        stock["character"] = max(1.0, float(stock.get("character", 0.0) or 0.0))

    for material in sorted(groups):
        group = sorted(
            groups[material],
            key=lambda source: (source.buffer_id, source.producer_id),
        )
        recipe = _direct_recipe(catalog, material)
        if recipe is None:
            refusals.append(
                Refusal(
                    code=REFUSAL_NO_DIRECT_PROCESSING_RECIPE,
                    detail=(
                        f"no enabled one-material processing recipe consumes {material}"
                    ),
                    evidence=tuple(source.evidence for source in group),
                )
            )
            continue

        dependency_planner = DependencyPlanner(
            catalog,
            machine_preference=("character",),
            excluded_machine_types=(),
        )
        processor = dependency_planner.machine_for_category(
            recipe.category,
            available=stock,
        )
        if processor is None:
            refusals.append(
                Refusal(
                    code=REFUSAL_NO_PROCESSOR_MACHINE,
                    detail=(
                        f"no runtime machine handles recipe category {recipe.category}"
                    ),
                    evidence=tuple(source.evidence for source in group),
                )
            )
            continue

        machine_dependency = dependency_planner.plan(
            processor,
            1.0,
            available=stock,
        )

        selected: tuple[str, PlacementPlan, DeliveryLink] | None = None
        for buffer_id in sorted({source.buffer_id for source in group}):
            entity = world_index.get(buffer_id)
            if entity is None:
                continue
            planned = _best_placement_delivery(
                processor=processor,
                source_entity=entity,
                world_entities=world_entities,
                footprints=footprints,
                reach=placement_reach,
                belt_budget=belt_budget,
            )
            if planned is not None:
                selected = (buffer_id, planned[0], planned[1])
                break
        if selected is None:
            refusals.append(
                Refusal(
                    code=REFUSAL_NO_PLACEMENT_DELIVERY,
                    detail=(
                        f"no placement+delivery option found for {material} -> "
                        f"{recipe.recipe_name} within reach/budget"
                    ),
                    evidence=tuple(source.evidence for source in group),
                    retriable=True,
                )
            )
            continue

        source_buffer, placement, delivery = selected
        product = recipe.products[0]
        graph_evidence = EvidenceRef(
            source="factory_graph",
            path="metrics.producers_reaching_processor",
            status=EvidenceStatus.OBSERVED,
        )
        catalog_evidence = EvidenceRef(
            source="runtime_catalog",
            path=f"recipes.{recipe.recipe_name}",
            status=EvidenceStatus.OBSERVED,
        )
        evidence = tuple(source.evidence for source in group) + (
            graph_evidence,
            catalog_evidence,
        )

        machine_ready = machine_dependency.feasible
        preconditions = (
            ActionCondition(
                name="producer_material_identity_observed",
                operator=ConditionOperator.EXISTS,
                state=ConditionState.SATISFIED,
                expected=material,
                evidence=tuple(source.evidence for source in group),
            ),
            ActionCondition(
                name="direct_processing_recipe_enabled",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.SATISFIED,
                expected=recipe.recipe_name,
                evidence=(catalog_evidence,),
            ),
            ActionCondition(
                name="processor_machine_resolved",
                operator=ConditionOperator.EQUALS,
                state=ConditionState.SATISFIED,
                expected=processor,
                evidence=(catalog_evidence,),
            ),
            ActionCondition(
                name="processor_constructible",
                operator=ConditionOperator.EQUALS,
                state=(
                    ConditionState.SATISFIED
                    if machine_ready
                    else ConditionState.UNSATISFIED
                ),
                expected=True,
                hard=True,
                evidence=(catalog_evidence,),
            ),
            ActionCondition(
                name="processor_placement_feasible",
                operator=ConditionOperator.EXISTS,
                state=ConditionState.SATISFIED,
                expected=placement.to_dict(),
                evidence=tuple(source.evidence for source in group),
            ),
            ActionCondition(
                name="buffer_to_processor_delivery_feasible",
                operator=ConditionOperator.EXISTS,
                state=ConditionState.SATISFIED,
                expected=delivery.to_dict(),
                evidence=tuple(source.evidence for source in group),
            ),
            ActionCondition(
                name="processor_energy_feasible",
                operator=ConditionOperator.EXISTS,
                state=ConditionState.UNKNOWN,
                expected=True,
                hard=False,
                evidence=(catalog_evidence,),
            ),
        )
        postconditions = request.postconditions or (
            ActionCondition(
                name="physical_factory_graph.producers_reaching_processor",
                operator=ConditionOperator.INCREASE,
                state=ConditionState.UNKNOWN,
                hard=True,
                evidence=(graph_evidence,),
            ),
        )

        child_request = ActionRequest(
            action_id=f"{request.action_id}:{material}",
            family=ActionFamily.PLACEMENT,
            intent=request.intent,
            provenance=_child_provenance(request),
            arguments={
                "material": material,
                "recipe": recipe.recipe_name,
                "product": product,
                "processor": processor,
                "source_buffer": source_buffer,
                "placement": placement.to_dict(),
                "delivery": delivery.to_dict(),
                "machine_dependency": machine_dependency.as_dict(),
            },
            targets=tuple(source.producer_id for source in group),
            requires=(material, processor),
            provides=(product,),
            evidence=evidence,
            preconditions=preconditions,
            postconditions=postconditions,
        )
        branches.append(
            ProcessingBranch(
                material=material,
                product=product,
                recipe=recipe,
                processor=processor,
                producers=tuple(source.producer_id for source in group),
                buffers=tuple(sorted({source.buffer_id for source in group})),
                source_buffer=source_buffer,
                machine_dependency=machine_dependency,
                placement=placement,
                delivery=delivery,
                request=child_request,
                preconditions=preconditions,
                postconditions=postconditions,
            )
        )

    branches_ready = bool(branches) and all(
        branch.executable_preconditions_satisfied for branch in branches
    )
    if branches_ready and not refusals:
        status = STATUS_READY
    elif branches:
        status = STATUS_PARTIAL
    else:
        status = STATUS_REFUSED
    return StructuralProcessingPlan(
        parent_request=request,
        sources=sources,
        branches=tuple(branches),
        refusals=tuple(refusals),
        status=status,
    )
