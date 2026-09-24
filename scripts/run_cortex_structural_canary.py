#!/usr/bin/env python3
"""One-shot controlled F2-E structural canary.

The canary intentionally replaces the current live lab world with a dedicated
non-confirmatory deterministic world. Frozen F1 evidence is not modified.

No execution occurs unless --execute is present.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.actions import ActionAuthority, ActionProvenance
from factorio_ai_lab.cortex.executor import request_from_repair_action
from factorio_ai_lab.cortex.structural import plan_processing_for_buffered_output
from factorio_ai_lab.cortex.structural_execute import StructuralTransactionalAdapter
from factorio_ai_lab.cortex.structural_prepare import prepare_structural_branch
from factorio_ai_lab.dashboard.state import FactorioObserver
from factorio_ai_lab.experiments.curriculum_runner import _runtime_entity_footprints
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    fast_reposition,
    list_environments,
)
from factorio_ai_lab.learning.factory_graph import (
    MATERIAL_RELATIONS,
    build_factory_graph,
)
from factorio_ai_lab.learning.repair_loop import (
    INTENT_PLACE_PROCESSING,
    TOOL_PLACEMENT,
    Prediction,
    RepairAction,
)
from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.planning.placement import (
    RESOURCE_PROTOTYPE_NAMES,
    ResourceSurvey,
)
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog
from factorio_ai_lab.runtime import FactorioWorldLease

DEFAULT_SEED = 424242
CONFIRMATORY_SEEDS = frozenset(range(20261101, 20261111))
DEFAULT_BOOTSTRAP_SETTLE_SECONDS = 5
DEFAULT_STRUCTURAL_SETTLE_SECONDS = 10


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def validate_canary_seed(seed: int) -> None:
    if seed in CONFIRMATORY_SEEDS:
        raise ValueError(
            f"seed {seed} is reserved for confirmatory evaluation and cannot "
            "be used by the F2-E canary"
        )


def patch_center(patch: Any) -> tuple[float, float]:
    box = patch.bounding_box
    return (
        (float(box.left_top.x) + float(box.right_bottom.x)) / 2.0,
        (float(box.left_top.y) + float(box.right_bottom.y)) / 2.0,
    )


def world_rows(namespace: Any, *, resources: bool) -> list[dict[str, Any]]:
    rows = namespace._save_entity_state(
        distance=500,
        player_entities=True,
        resource_entities=resources,
        items_on_ground=False,
        encode=False,
        compress=False,
    )
    return [row for row in rows if isinstance(row, dict)]


def player_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("name") or "") not in RESOURCE_PROTOTYPE_NAMES
        and str(row.get("type") or "") != "resource"
    ]


def resource_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("name") or "") in RESOURCE_PROTOTYPE_NAMES
        or str(row.get("type") or "") == "resource"
    ]


def structural_targets(graph: dict[str, Any]) -> list[str]:
    nodes = {
        str(row.get("id")): row
        for row in graph.get("nodes", [])
        if isinstance(row, dict) and row.get("id")
    }
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("edges", []):
        if (
            isinstance(edge, dict)
            and edge.get("relation") in MATERIAL_RELATIONS
        ):
            adjacency[str(edge["source"])].add(str(edge["target"]))
    processors = {
        node_id
        for node_id, row in nodes.items()
        if row.get("category") == "processing"
    }

    def downstream(source: str) -> set[str]:
        seen = {source}
        queue = deque([source])
        reached: set[str] = set()
        while queue:
            current = queue.popleft()
            for target in adjacency.get(current, ()):
                if target in seen:
                    continue
                seen.add(target)
                reached.add(target)
                queue.append(target)
        return reached

    return [
        node_id
        for node_id, row in sorted(nodes.items())
        if row.get("category") == "extraction"
        and (reached := downstream(node_id))
        and not (reached & processors)
    ]


def entity_position(row: dict[str, Any]) -> tuple[float, float] | None:
    position = row.get("position")
    if not isinstance(position, dict):
        return None
    x = position.get("x")
    y = position.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    return float(x), float(y)


def processor_row(
    prepared: Any,
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    processor = str(prepared.preflight.get("processor") or "")
    placement = prepared.preflight.get("placement")
    if not isinstance(placement, dict):
        return None
    position = placement.get("position")
    if not isinstance(position, dict):
        return None
    x = position.get("x")
    y = position.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    target = (float(x), float(y))
    candidates = []
    for row in rows:
        if str(row.get("name") or "") != processor:
            continue
        pos = entity_position(row)
        if pos is None:
            continue
        distance = math.hypot(pos[0] - target[0], pos[1] - target[1])
        candidates.append((distance, row))
    if not candidates:
        return None
    distance, row = min(candidates, key=lambda item: item[0])
    return row if distance <= 1.0 else None


def runtime_catalog(instance: Any) -> RuntimeFactorioCatalog:
    raw = instance.rcon_client.send_command(
        FactorioObserver._GAME_KNOWLEDGE_COMMAND
    )
    if not raw:
        raise RuntimeError("live prototype catalog returned no payload")
    return RuntimeFactorioCatalog(json.loads(raw))


def available_inventory(rows: list[dict[str, Any]]) -> dict[str, float]:
    """On-hand player items; placed world entities are not inventory."""

    counts: Counter[str] = Counter()
    character_seen = False
    for row in rows:
        if str(row.get("name") or "") != "character":
            continue
        character_seen = True
        inventory = row.get("inventory")
        if not isinstance(inventory, dict):
            continue
        for name, value in inventory.items():
            if (
                isinstance(name, str)
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
                and float(value) > 0
            ):
                counts[name] += float(value)
    if character_seen:
        counts["character"] = max(1.0, float(counts.get("character", 0.0)))
    return {name: float(count) for name, count in sorted(counts.items())}


def resource_survey_from_overview(payload: dict[str, Any]) -> ResourceSurvey:
    """Convert the canonical RCON resource instrument into planner evidence."""

    points = [
        {
            "name": str(point["name"]),
            "type": "resource",
            "position": {
                "x": float(point["x"]),
                "y": float(point["y"]),
            },
        }
        for point in payload.get("points", [])
        if isinstance(point, dict)
        and point.get("name")
        and isinstance(point.get("x"), (int, float))
        and isinstance(point.get("y"), (int, float))
    ]
    center = payload.get("center")
    radius = payload.get("radius")
    surveyed = None
    if isinstance(center, dict) and isinstance(radius, (int, float)):
        x = center.get("x")
        y = center.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            r = float(radius)
            surveyed = (float(x) - r, float(y) - r, float(x) + r, float(y) + r)
    return ResourceSurvey.from_entities(points, surveyed=surveyed)


def craft_output_count(row: dict[str, Any] | None, product: str) -> float:
    if row is None:
        return 0.0
    output = row.get("craft_output")
    if not isinstance(output, list):
        return 0.0
    total = 0.0
    for stack in output:
        if not isinstance(stack, dict) or str(stack.get("name") or "") != product:
            continue
        count = stack.get("count")
        if isinstance(count, (int, float)) and not isinstance(count, bool):
            total += float(count)
    return total


def build_measurement_probe(observer: FactorioObserver):
    def measure(prepared: Any) -> dict[str, Any]:
        snapshot = observer.snapshot()
        if snapshot.get("connected") is not True:
            raise RuntimeError(
                "canonical world snapshot unavailable: "
                + str(snapshot.get("error") or "connected=false")
            )
        rows = [
            row
            for row in snapshot.get("entities", [])
            if isinstance(row, dict)
        ]
        graph = build_factory_graph(rows)
        metrics = graph.get("metrics", {})
        processor = processor_row(prepared, rows)
        exists = processor is not None
        product = str(prepared.preflight.get("product") or "")
        return {
            "producers_reaching_processor": metrics.get(
                "producers_reaching_processor"
            ),
            "physical_processing_coverage": metrics.get(
                "physical_processing_coverage"
            ),
            "processor_exists": exists,
            "processor_status": (
                None if processor is None else processor.get("status")
            ),
            "processor_output": craft_output_count(processor, product),
        }

    return measure


def write_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def run_canary(
    *,
    seed: int,
    bootstrap_settle_seconds: int,
    structural_settle_seconds: int,
    artifact: Path,
) -> dict[str, Any]:
    validate_canary_seed(seed)
    import gym

    revision = code_revision()
    if revision.get("dirty") is not False:
        raise RuntimeError(
            "F2-E canary requires a clean committed source tree"
        )

    run_id = datetime.now(UTC).strftime("cortex-f2e-%Y%m%dT%H%M%SZ")
    record: dict[str, Any] = {
        "schema_version": "cortex_f2e_structural_canary_v1",
        "run_id": run_id,
        "seed": seed,
        "confirmatory_seed": False,
        "authority": ActionAuthority.EXECUTE.value,
        "continuous_authority": False,
        "code_revision": revision,
        "started_at": utc_now(),
        "status": "starting",
        "world_replaced_by_canary": True,
    }

    env = None
    observer = None
    with FactorioWorldLease(
        run_id=run_id,
        arena="cortex_f2e_canary",
        owner="run_cortex_structural_canary",
    ):
        try:
            list_environments()
            env = gym.make("iron_ore_throughput", run_idx=0)
            executor = TransactionalFLEExecutor(env)
            executor.reset(seed=seed)
            instance = env.unwrapped.instance
            namespace = instance.namespace

            discovery = executor.execute(
                """
iron=nearest(Resource.IronOre)
patch=get_resource_patch(Resource.IronOre,iron,radius=30)
print({'iron':iron,'patch':patch})
""",
                accept=lambda step: (
                    not bool(step.info.get("error_occurred"))
                    and step.candidate_game_state is not None
                ),
            )
            if not discovery.accepted or namespace.patch is None:
                raise RuntimeError("canary iron discovery was rejected")

            x, y = patch_center(namespace.patch)
            fast_reposition(env, x=x, y=y)

            bootstrap_code = f"""
drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={x!r},y={y!r}),
    direction=Direction.DOWN,
)
drill=insert_item(Prototype.Coal,drill,quantity=20)
chest=place_entity_next_to(
    Prototype.WoodenChest,
    drill.position,
    direction=Direction.DOWN,
)
sleep({int(bootstrap_settle_seconds)})
canary_iron=inspect_inventory(chest)[Prototype.IronOre]
print({{'canary_iron':canary_iron}})
"""
            bootstrap = executor.execute(
                bootstrap_code,
                accept=lambda step: (
                    not bool(step.info.get("error_occurred"))
                    and step.candidate_game_state is not None
                    and float(getattr(namespace, "canary_iron", 0) or 0) > 0
                ),
                use_checkpoint_for_action=False,
                purpose="infrastructure",
            )
            if not bootstrap.accepted:
                raise RuntimeError("producer+buffer bootstrap was rejected")

            fle_rows = world_rows(namespace, resources=False)
            available = available_inventory(fle_rows)

            observer = FactorioObserver()
            snapshot = observer.snapshot()
            overview = observer.resource_overview(max_age_s=0)
            knowledge = observer.game_knowledge(max_age_s=0)
            if snapshot.get("connected") is not True:
                raise RuntimeError(
                    "canonical world snapshot unavailable after bootstrap: "
                    + str(snapshot.get("error") or "connected=false")
                )
            if overview.get("connected") is not True:
                raise RuntimeError(
                    "canonical resource survey unavailable after bootstrap: "
                    + str(overview.get("error") or "connected=false")
                )

            machines = [
                row
                for row in snapshot.get("entities", [])
                if isinstance(row, dict)
            ]
            resources = resource_survey_from_overview(overview)
            graph = build_factory_graph(machines)
            targets = structural_targets(graph)
            if not targets:
                raise RuntimeError("canary produced no structural target")

            repair = RepairAction(
                tool=TOOL_PLACEMENT,
                intent=INTENT_PLACE_PROCESSING,
                prediction=Prediction(
                    "physical_factory_graph.producers_reaching_processor",
                    "increase",
                ),
                provides=("material",),
                targets=tuple(targets),
                arguments={"producers": targets},
            )
            request = request_from_repair_action(
                repair,
                action_id=f"{run_id}:structural",
                provenance=ActionProvenance(
                    requested_by="F2-E-controlled-canary",
                    source_component=(
                        "scripts.run_cortex_structural_canary"
                    ),
                    code_revision=str(revision.get("commit") or "unknown"),
                    run_id=run_id,
                ),
            )
            plan = plan_processing_for_buffered_output(
                request,
                graph=graph,
                world_entities=machines,
                catalog=RuntimeFactorioCatalog(knowledge),
                available=available,
                footprints=_runtime_entity_footprints(instance),
                resources=resources,
            )
            record.update(
                {
                    "bootstrap": {
                        "accepted": bootstrap.accepted,
                        "iron_buffered": float(namespace.canary_iron or 0),
                        "patch_center": {"x": x, "y": y},
                    },
                    "planning_instruments": {
                        "world": "FactorioObserver.snapshot",
                        "resources": "FactorioObserver.resource_overview",
                        "catalog": "FactorioObserver.game_knowledge",
                        "available": "FLE character inventory",
                    },
                    "available_inventory": available,
                    "graph_before": graph.get("metrics", {}),
                    "targets": targets,
                    "plan": plan.to_dict(),
                }
            )
            ready = [
                branch
                for branch in plan.branches
                if branch.executable_preconditions_satisfied
            ]
            if len(ready) != 1:
                raise RuntimeError(
                    f"canary expected exactly one ready branch, got {len(ready)}"
                )
            preparation = prepare_structural_branch(ready[0])
            if not preparation.ready or preparation.prepared is None:
                raise RuntimeError(
                    "ready branch failed PreparedStructuralAction compilation"
                )
            prepared = preparation.prepared

            placement = prepared.preflight.get("placement")
            position = placement.get("position") if isinstance(placement, dict) else None
            if not isinstance(position, dict):
                raise TypeError("prepared canary action has no placement position")
            fast_reposition(
                env,
                x=float(position["x"]),
                y=float(position["y"]),
            )

            sync = executor.execute(
                "print({'cortex_canary_checkpoint':True})",
                accept=lambda step: (
                    not bool(step.info.get("error_occurred"))
                    and step.candidate_game_state is not None
                ),
                use_checkpoint_for_action=False,
                purpose="infrastructure",
            )
            if not sync.accepted:
                raise RuntimeError("failed to serialize pre-action canary checkpoint")

            measure = build_measurement_probe(observer)
            before = dict(measure(prepared))
            result = StructuralTransactionalAdapter().execute(
                prepared,
                authority=ActionAuthority.EXECUTE,
                executor=executor,
                measure=measure,
                use_checkpoint_for_action=True,
                settle_seconds=structural_settle_seconds,
            )
            final = dict(measure(prepared))

            record.update(
                {
                    "status": "completed",
                    "finished_at": utc_now(),
                    "prepared": prepared.to_dict(),
                    "measurement_before": before,
                    "action_result": result.to_dict(),
                    "measurement_final": final,
                    "interventions": executor.intervention_snapshot(),
                    "transaction_committed": (
                        result.status.value == "accepted"
                    ),
                    "rollback_observed": (
                        result.status.value == "rejected"
                        and final == before
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            record.update(
                {
                    "status": "failed",
                    "finished_at": utc_now(),
                    "failure": {
                        "type": type(exc).__name__,
                        "detail": str(exc),
                    },
                }
            )
        finally:
            if observer is not None:
                observer.close()
            if env is not None:
                env.close()

    write_artifact(artifact, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--bootstrap-settle-seconds",
        type=int,
        default=DEFAULT_BOOTSTRAP_SETTLE_SECONDS,
    )
    parser.add_argument(
        "--structural-settle-seconds",
        type=int,
        default=DEFAULT_STRUCTURAL_SETTLE_SECONDS,
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=RUNS_DIR / "audits" / "cortex_f2e_structural_canary.json",
    )
    args = parser.parse_args()

    validate_canary_seed(args.seed)
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": "pass --execute for the one-shot F2-E canary",
                    "seed": args.seed,
                    "world_mutation": False,
                },
                indent=2,
            )
        )
        return 2

    record = run_canary(
        seed=args.seed,
        bootstrap_settle_seconds=args.bootstrap_settle_seconds,
        structural_settle_seconds=args.structural_settle_seconds,
        artifact=args.artifact,
    )
    print(json.dumps(record, indent=2, sort_keys=True, default=str))
    return 0 if record.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
