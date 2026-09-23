from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.experiments.curriculum_runner import (
    EVOLUTION_CHAMPION,
    ResearchJournal,
    append_jsonl,
    atomic_json,
    read_json_object,
    utc_now,
)
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    enforce_minimum_eval_timeout,
    fast_reposition,
    intervention_delta,
    list_environments,
)
from factorio_ai_lab.learning.autonomy import evaluate_factory_autonomy
from factorio_ai_lab.learning.checkpoints import save_game_state
from factorio_ai_lab.learning.robustness import OpenPlayRobustnessGate
from factorio_ai_lab.planning.factorio_catalog import (
    EARLY_GAME_PRODUCTION_PLANNER,
)
from factorio_ai_lab.planning.materials import MaterialLedger
from factorio_ai_lab.runtime import FactorioWorldLease

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
VALIDATED_CHAMPION = RUNS_DIR / "open_play_validated_champion.json"
OPEN_PLAY_HISTORY = RUNS_DIR / "open_play_validation_history.jsonl"
OPEN_PLAY_ROBUSTNESS_STATE = RUNS_DIR / "open_play_robustness_state.json"
OPEN_PLAY_CHECKPOINTS = RUNS_DIR / "checkpoints" / "open_play"
LIFELONG_CHECKPOINT = RUNS_DIR / "lifelong_champion_state.json"


OPEN_PLAY_CURRICULUM = [
    {
        "name": "Raw bootstrap",
        "status": "pending",
        "detail": "Harvest only world resources needed for the first furnaces.",
    },
    {
        "name": "Technology triggers",
        "status": "pending",
        "detail": (
            "Smelt 50 iron plates and 10+ copper plates to unlock Steam Power "
            "and Electronics through Factorio 2.0 research triggers."
        ),
    },
    {
        "name": "Coal commissioning",
        "status": "pending",
        "detail": "Commission a burner coal cell; manual refuel is tracked and does not count as autonomy.",
    },
    {
        "name": "Steam commissioning",
        "status": "pending",
        "detail": "Commission offshore pump, boiler and steam engine; sustained autonomy is validated later.",
    },
    {
        "name": "Lab bootstrap",
        "status": "pending",
        "detail": (
            "Craft a lab to trigger Automation Science Pack, power it and "
            "research Automation with red science."
        ),
    },
    {
        "name": "Powered red science",
        "status": "pending",
        "detail": (
            "Use an assembling machine unlocked by Automation to produce "
            "automation science electrically."
        ),
    },
    {
        "name": "Electric mining transition",
        "status": "pending",
        "detail": (
            "Research electric mining first, then replace commissioning flows "
            "with physical coal-to-boiler and ore-to-furnace logistics."
        ),
    },
    {
        "name": "Logistic-science unlock",
        "status": "pending",
        "detail": (
            "Scale powered red-science output on the surviving physical power "
            "backbone and research Logistic Science Pack."
        ),
    },
    {
        "name": "Green-science industry",
        "status": "pending",
        "detail": (
            "Build powered cable, circuit, belt and inserter cells and consume "
            "their output in a logistic-science assembler."
        ),
    },
    {
        "name": "Logistics research",
        "status": "pending",
        "detail": (
            "Research Logistics from its real 20-red-science requirement after "
            "green industry has been independently demonstrated."
        ),
    },
    {
        "name": "Autonomy soak",
        "status": "pending",
        "detail": (
            "Run without harvest/insert/extract intervention and require "
            "closed-loop fuel, power and material logistics."
        ),
    },
]

OPEN_PLAY_STAGE_INDEX = {
    stage["name"]: index
    for index, stage in enumerate(OPEN_PLAY_CURRICULUM)
}
STAGE_RAW_BOOTSTRAP = OPEN_PLAY_STAGE_INDEX["Raw bootstrap"]
STAGE_TECHNOLOGY_TRIGGERS = OPEN_PLAY_STAGE_INDEX["Technology triggers"]
STAGE_COAL_COMMISSIONING = OPEN_PLAY_STAGE_INDEX["Coal commissioning"]
STAGE_STEAM_COMMISSIONING = OPEN_PLAY_STAGE_INDEX["Steam commissioning"]
STAGE_LAB_BOOTSTRAP = OPEN_PLAY_STAGE_INDEX["Lab bootstrap"]
STAGE_POWERED_RED_SCIENCE = OPEN_PLAY_STAGE_INDEX["Powered red science"]
STAGE_ELECTRIC_MINING_TRANSITION = OPEN_PLAY_STAGE_INDEX["Electric mining transition"]
STAGE_LOGISTIC_SCIENCE_UNLOCK = OPEN_PLAY_STAGE_INDEX["Logistic-science unlock"]
STAGE_GREEN_SCIENCE_INDUSTRY = OPEN_PLAY_STAGE_INDEX["Green-science industry"]
STAGE_LOGISTICS_RESEARCH = OPEN_PLAY_STAGE_INDEX["Logistics research"]
STAGE_AUTONOMY_SOAK = OPEN_PLAY_STAGE_INDEX["Autonomy soak"]


def _autonomy_layout_directions(
    variant: int,
) -> tuple[str, str, str, str, str]:
    layouts = {
        0: ("RIGHT", "LEFT", "RIGHT", "LEFT", "RIGHT"),
        1: ("LEFT", "RIGHT", "LEFT", "LEFT", "RIGHT"),
        2: ("RIGHT", "LEFT", "RIGHT", "RIGHT", "LEFT"),
        3: ("LEFT", "RIGHT", "LEFT", "RIGHT", "LEFT"),
    }
    return layouts[int(variant) % len(layouts)]


def _opposite_direction_name(direction: str) -> str:
    opposites = {
        "UP": "DOWN",
        "RIGHT": "LEFT",
        "DOWN": "UP",
        "LEFT": "RIGHT",
    }
    try:
        return opposites[direction]
    except KeyError as exc:
        raise ValueError(f"unsupported cardinal direction: {direction}") from exc


def _production_counter(namespace: Any, item: str) -> float:
    stats = namespace._get_production_stats()
    output = stats.get("output", {})
    if not isinstance(output, dict):
        return 0.0
    return float(output.get(item, 0.0) or 0.0)


def _autonomy_entity_snapshot(instance: Any) -> list[dict[str, Any]]:
    command = r"""
/c local p=storage.agent_characters and storage.agent_characters[1]
if not p then rcon.print("[]") return end
local rows={}
for _,e in pairs(p.surface.find_entities_filtered{force=p.force}) do
  if e.valid then
    local row={
      name=e.name,
      type=e.type,
      position={x=e.position.x,y=e.position.y}
    }
    local status=e.status
    if status then
      row.status=tostring(status)
      for key,value in pairs(defines.entity_status) do
        if value==status then row.status=key break end
      end
    end
    rows[#rows+1]=row
  end
end
rcon.print(helpers.table_to_json(rows))
"""
    raw = instance.rcon_client.send_command(command)
    parsed = json.loads(str(raw or "[]"))
    if not isinstance(parsed, list):
        return []
    return [row for row in parsed if isinstance(row, dict)]


def _record_interventions(
    executor: TransactionalFLEExecutor,
    journal: ResearchJournal,
    *,
    bootstrap_baseline: dict[str, dict[str, int]] | None = None,
    autonomy_baseline: dict[str, dict[str, int]] | None = None,
    assisted_navigation_count: int = 0,
) -> dict[str, Any]:
    snapshot = executor.intervention_snapshot()
    payload: dict[str, Any] = {
        "attempted": snapshot.get("attempted", {}),
        "committed": snapshot.get("committed", {}),
        "assisted_navigation_count": assisted_navigation_count,
    }
    if bootstrap_baseline is not None:
        payload["post_bootstrap_committed"] = intervention_delta(
            snapshot.get("committed", {}),
            bootstrap_baseline.get("committed", {}),
        )
        payload["post_bootstrap_attempted"] = intervention_delta(
            snapshot.get("attempted", {}),
            bootstrap_baseline.get("attempted", {}),
        )
    if autonomy_baseline is not None:
        payload["autonomy_window_committed"] = intervention_delta(
            snapshot.get("committed", {}),
            autonomy_baseline.get("committed", {}),
        )
        payload["autonomy_window_attempted"] = intervention_delta(
            snapshot.get("attempted", {}),
            autonomy_baseline.get("attempted", {}),
        )
    journal.state["metrics"]["interventions"] = payload
    return payload


def _closed_loop_validation_passed(
    pipeline_ok: bool,
    metrics: dict[str, Any],
) -> bool:
    autonomy = metrics.get("autonomy", {})
    return bool(
        pipeline_ok
        and isinstance(autonomy, dict)
        and autonomy.get("closed_loop") is True
    )


def _technology_researched(instance: Any, name: str) -> bool:
    command = (
        "/c local p=storage.agent_characters and storage.agent_characters[1]; "
        "if not p then rcon.print('false') return end; "
        f"local t=p.force.technologies['{name}']; "
        "rcon.print(t and t.researched and 'true' or 'false')"
    )
    raw = instance.rcon_client.send_command(command)
    return str(raw).strip().lower() == "true"


def _prepare_journal(run_id: str, champion: dict[str, Any]) -> ResearchJournal:
    journal = ResearchJournal(run_id)
    journal.state.update(
        {
            "arena": {
                "mode": "open_play",
                "environment": "open_play",
                "inventory": "empty",
                "technology": "factorio_2_real_triggers",
                "promotion_scope": "validated_champion",
            },
            "objective": (
                "Validate the experimental champion in an empty-inventory "
                "Factorio 2.0 technology progression world"
            ),
            "detail": "Bootstrapping open-play validation.",
            "stage": "bootstrap",
            "progress": 0.0,
            "next_action": "harvest initial resources from the live world",
            "curriculum": [dict(item) for item in OPEN_PLAY_CURRICULUM],
            "online_learning": {
                "algorithm": "open_play_survival_validation",
                "status": "validating",
                "history": [],
                "best_arm": champion.get("configuration", {}).get(
                    "placement_best_arm"
                ),
                "arms": {},
            },
            "engineering_progression": {
                "status": "validating_champion",
                "achieved": [],
                "stalled_attempts": {},
                "frontier": [],
                "next_goal": None,
            },
            "resource_accounting": {
                "exogenous_inputs": {},
                "bootstrap_mode": "world_harvest_only",
            },
            "evolution": {
                "scheme": "lab_champion_then_open_play_validation",
                "generation": champion.get("generation"),
                "retention_ratio": 0.80,
                "champion": champion,
                "challenger": {
                    "run_id": run_id,
                    "status": "open_play_validation",
                    "fitness": None,
                    "configuration": champion.get("configuration", {}),
                },
                "promotion": None,
            },
            "metrics": {},
            "events": [],
        }
    )
    journal.flush()
    return journal


def _bootstrap_resource_deficits(
    measured: dict[str, float],
    requested: dict[str, int],
) -> dict[str, float]:
    return {
        key: max(0.0, float(target) - float(measured.get(key, 0.0)))
        for key, target in requested.items()
    }


def _bootstrap_raw(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        STAGE_RAW_BOOTSTRAP,
        status="running",
        detail=(
            "Harvesting world resources incrementally with transactional "
            "checkpoints and replanning between depleted patches."
        ),
        next_action="create the parallel bootstrap smelter bank",
    )
    champion = journal.state.get("evolution", {}).get("champion") or {}
    configuration = (
        champion.get("configuration", {})
        if isinstance(champion, dict)
        else {}
    )
    requested = {
        "wood": int(configuration.get("open_play_wood_target", 50) or 50),
        "stone": int(configuration.get("open_play_stone_target", 50) or 50),
        "coal": int(configuration.get("open_play_coal_target", 100) or 100),
        "iron": int(configuration.get("open_play_iron_target", 300) or 300),
        "copper": int(configuration.get("open_play_copper_target", 160) or 160),
    }
    wood_radius = int(configuration.get("open_play_wood_radius", 24) or 24)
    thresholds = {
        "wood": 20.0,
        "stone": 30.0,
        "coal": 60.0,
        "iron": 260.0,
        "copper": 120.0,
    }
    resource_specs = {
        "wood": ("Resource.Wood", "Prototype.Wood", 12),
        "stone": ("Resource.Stone", "Prototype.Stone", 30),
        "coal": ("Resource.Coal", "Prototype.Coal", 40),
        "iron": ("Resource.IronOre", "Prototype.IronOre", 60),
        "copper": ("Resource.CopperOre", "Prototype.CopperOre", 50),
    }

    measured: dict[str, float] = {key: 0.0 for key in thresholds}
    diagnostics: dict[str, Any] = {}
    total_reward = 0.0
    action_error = False
    path_service_failed = False
    assisted_navigation_count = 0

    def relocate(
        resource_name: str,
        resource_key: str,
    ) -> tuple[bool, str, dict[str, Any]]:
        nonlocal total_reward
        nonlocal action_error
        nonlocal path_service_failed
        nonlocal assisted_navigation_count

        locate_code = f"""
bootstrap_pos=nearest({resource_name})
{resource_key}_pos=bootstrap_pos
print(bootstrap_pos)
"""
        locate_step = executor.execute(
            locate_code,
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        total_reward += locate_step.reward
        resource_position = getattr(namespace, "bootstrap_pos", None)
        movement: dict[str, Any] = {
            "locate_result": str(locate_step.info.get("result", ""))[:500],
        }
        if not locate_step.accepted or resource_position is None:
            action_error = True
            movement["error"] = "location_failed"
            return False, "location_failed", movement

        movement["target"] = {
            "x": float(resource_position.x),
            "y": float(resource_position.y),
        }

        if path_service_failed:
            reposition = fast_reposition(
                env,
                x=float(resource_position.x) + 1.0,
                y=float(resource_position.y) + 1.0,
            )
            assisted_navigation_count += 1
            movement["reposition"] = {
                "x": reposition.x,
                "y": reposition.y,
            }
            return True, "fast_reposition_fallback", movement

        move_code = """move_to(bootstrap_pos)
print(player_location)"""
        move_step = executor.execute(
            move_code,
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        total_reward += move_step.reward
        move_result = str(move_step.info.get("result", ""))
        movement["move_result"] = move_result[:500]
        if move_step.accepted:
            return True, "fle_move_to", movement

        if "Path request timed out" in move_result:
            path_service_failed = True
            reposition = fast_reposition(
                env,
                x=float(resource_position.x) + 1.0,
                y=float(resource_position.y) + 1.0,
            )
            assisted_navigation_count += 1
            movement["reposition"] = {
                "x": reposition.x,
                "y": reposition.y,
            }
            movement["fallback_reason"] = "fle_path_timeout"
            return True, "fast_reposition_fallback", movement

        action_error = True
        movement["error"] = "move_failed"
        return False, "move_failed", movement

    for key in ("wood", "stone", "coal", "iron", "copper"):
        resource_name, prototype_name, chunk_size = resource_specs[key]
        target = requested[key]
        attempts: list[dict[str, Any]] = []
        navigation_modes: set[str] = set()
        previous = measured[key]

        for attempt in range(1, 21):
            remaining = max(0, target - int(previous))
            if remaining <= 0:
                break

            positioned, navigation_mode, movement = relocate(
                resource_name,
                key,
            )
            navigation_modes.add(navigation_mode)
            if not positioned:
                attempts.append(
                    {
                        "attempt": attempt,
                        "accepted": False,
                        "movement": movement,
                        "error": "navigation_failed",
                    }
                )
                break

            # Keep every harvest bounded. Large one-shot harvests can consume
            # the reachable part of a patch and then leave a stale/awkward
            # relocation point; bounded chunks force a fresh nearest()+move_to
            # cycle between local depletion events.
            chunk = min(chunk_size, remaining)
            radius_clause = f",radius={wood_radius}" if key == "wood" else ""
            code = f"""
bootstrap_pos=nearest({resource_name})
harvest_resource(
    bootstrap_pos,
    quantity={chunk}{radius_clause},
)
bootstrap_inventory=inspect_inventory()
bootstrap_value=bootstrap_inventory[{prototype_name}]
print(bootstrap_value)
"""

            def accept(
                result: Any,
                *,
                resource_key: str = key,
                previous_value: float = previous,
            ) -> bool:
                value = float(getattr(namespace, "bootstrap_value", 0) or 0)
                measured[resource_key] = value
                return (
                    not bool(result.info.get("error_occurred"))
                    and result.candidate_game_state is not None
                    and value > previous_value
                )

            step = executor.execute(
                code,
                accept=accept,
                use_checkpoint_for_action=False,
            )
            total_reward += step.reward
            result_text = str(step.info.get("result", ""))
            attempts.append(
                {
                    "attempt": attempt,
                    "requested_chunk": chunk,
                    "before": previous,
                    "after": measured[key],
                    "accepted": step.accepted,
                    "navigation_mode": navigation_mode,
                    "movement": movement,
                    "error_occurred": bool(step.info.get("error_occurred")),
                    "result": result_text[:500],
                    "ticks": step.info.get("ticks"),
                    "policy_execution_time": step.info.get(
                        "policy_execution_time"
                    ),
                }
            )
            if not step.accepted:
                action_error = bool(step.info.get("error_occurred"))
                break

            previous = measured[key]
            if measured[key] >= target:
                break

        diagnostics[key] = {
            "target": target,
            "threshold": thresholds[key],
            "collected": measured[key],
            "navigation_modes": sorted(navigation_modes),
            "attempts": attempts,
        }
        journal.event(
            "bootstrap_progress",
            f"Open-play harvested {measured[key]:.0f}/{target} {key}.",
            resource=key,
            collected=measured[key],
            target=target,
            threshold=thresholds[key],
            attempts=len(attempts),
            navigation_modes=sorted(navigation_modes),
        )
        if measured[key] < thresholds[key]:
            break

    threshold_deficits = {
        key: max(0.0, threshold - measured.get(key, 0.0))
        for key, threshold in thresholds.items()
    }
    deficits = _bootstrap_resource_deficits(measured, requested)
    navigation_assisted = assisted_navigation_count > 0
    journal.state["metrics"]["open_play_bootstrap"] = {
        "collected": measured,
        "thresholds": thresholds,
        "requested": {
            **requested,
            "wood_radius": wood_radius,
        },
        "deficits": deficits,
        "threshold_deficits": threshold_deficits,
        "engine_reward": total_reward,
        "error_occurred": action_error,
        "navigation_assisted": navigation_assisted,
        "assisted_navigation_count": assisted_navigation_count,
        "resource_diagnostics": diagnostics,
    }
    journal.state.setdefault("arena", {})["navigation"] = (
        "fle_move_to_with_fast_reposition_fallback"
        if navigation_assisted
        else "fle_move_to"
    )

    limiting = [
        key
        for key, deficit in deficits.items()
        if deficit > 0
    ]
    if limiting:
        journal.fail_stage(
            STAGE_RAW_BOOTSTRAP,
            "Open-play raw-resource bootstrap failed: "
            + "resource deficits="
            + ",".join(limiting),
        )
        journal.event(
            "counterexample",
            "Open-play incremental bootstrap rejected with typed resource deficits.",
            collected=measured,
            thresholds=thresholds,
            requested={
                **requested,
                "wood_radius": wood_radius,
            },
            deficits=deficits,
            diagnostics=diagnostics,
            navigation_assisted=navigation_assisted,
        )
        return False

    journal.complete_stage(
        STAGE_RAW_BOOTSTRAP,
        (
            "Incremental open-play bootstrap harvested every requested world "
            "resource target without benchmark inventory."
        ),
    )
    journal.event(
        "accept",
        "Open-play bootstrap uses only harvested world resources.",
        collected=measured,
        requested={
            **requested,
            "wood_radius": wood_radius,
        },
        diagnostics=diagnostics,
        navigation_assisted=navigation_assisted,
    )
    return True


def _technology_triggers(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    instance = env.unwrapped.instance
    namespace = instance.namespace
    journal.set_stage(
        STAGE_TECHNOLOGY_TRIGGERS,
        status="validating",
        detail=(
            "Smelting enough iron/copper to exercise Factorio 2.0 Steam Power "
            "and Electronics craft-item triggers."
        ),
        next_action="validate Steam Power and Electronics triggers",
    )
    code = f"""
craft_item(Prototype.StoneFurnace,quantity=5)
single_furnace_box=BuildingBox(
    width=Prototype.StoneFurnace.WIDTH+4,
    height=Prototype.StoneFurnace.HEIGHT+4,
)

furnace_area=nearest_buildable(
    Prototype.StoneFurnace,
    single_furnace_box,
    copper_pos,
)
move_to(furnace_area.center)
iron_furnace=place_entity(
    Prototype.StoneFurnace,
    position=furnace_area.center,
)

furnace_area_2=nearest_buildable(
    Prototype.StoneFurnace,
    single_furnace_box,
    copper_pos,
)
move_to(furnace_area_2.center)
iron_furnace_2=place_entity(
    Prototype.StoneFurnace,
    position=furnace_area_2.center,
)

furnace_area_3=nearest_buildable(
    Prototype.StoneFurnace,
    single_furnace_box,
    copper_pos,
)
move_to(furnace_area_3.center)
iron_furnace_3=place_entity(
    Prototype.StoneFurnace,
    position=furnace_area_3.center,
)

furnace_area_4=nearest_buildable(
    Prototype.StoneFurnace,
    single_furnace_box,
    copper_pos,
)
move_to(furnace_area_4.center)
copper_furnace=place_entity(
    Prototype.StoneFurnace,
    position=furnace_area_4.center,
)

furnace_area_5=nearest_buildable(
    Prototype.StoneFurnace,
    single_furnace_box,
    copper_pos,
)
move_to(furnace_area_5.center)
copper_furnace_2=place_entity(
    Prototype.StoneFurnace,
    position=furnace_area_5.center,
)

for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    furnace=insert_item(Prototype.Coal,furnace,quantity=8)
    furnace=insert_item(Prototype.IronOre,furnace,quantity=100)
for furnace in (copper_furnace,copper_furnace_2):
    furnace=insert_item(Prototype.Coal,furnace,quantity=8)
    furnace=insert_item(Prototype.CopperOre,furnace,quantity=80)

sleep({settle_seconds})
extracted_iron=0
for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    count=inspect_inventory(furnace)[Prototype.IronPlate]
    if count>0:
        extracted_iron+=extract_item(
            Prototype.IronPlate,
            furnace,
            quantity=count,
        )
extracted_copper=0
for furnace in (copper_furnace,copper_furnace_2):
    count=inspect_inventory(furnace)[Prototype.CopperPlate]
    if count>0:
        extracted_copper+=extract_item(
            Prototype.CopperPlate,
            furnace,
            quantity=count,
        )
print({{
  'iron_plates':extracted_iron,
  'copper_plates':extracted_copper,
}})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "extracted_iron", 0) or 0) >= 50
            and float(getattr(namespace, "extracted_copper", 0) or 0) >= 10
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    steam = _technology_researched(instance, "steam-power")
    electronics = _technology_researched(instance, "electronics")
    iron_plates = float(getattr(namespace, "extracted_iron", 0) or 0)
    copper_plates = float(getattr(namespace, "extracted_copper", 0) or 0)
    trigger_diagnostics = {
        "accepted": step.accepted,
        "error_occurred": bool(step.info.get("error_occurred")),
        "result": str(step.info.get("result", ""))[:1200],
        "ticks": step.info.get("ticks"),
        "policy_execution_time": step.info.get("policy_execution_time"),
        "iron_plates": iron_plates,
        "copper_plates": copper_plates,
        "steam_power_triggered": steam,
        "electronics_triggered": electronics,
    }
    journal.state["metrics"].update(
        {
            "open_play_iron_plates": iron_plates,
            "open_play_copper_plates": copper_plates,
            "steam_power_triggered": steam,
            "electronics_triggered": electronics,
            "technology_trigger_diagnostics": trigger_diagnostics,
        }
    )
    if not step.accepted or not steam or not electronics:
        journal.fail_stage(
            STAGE_TECHNOLOGY_TRIGGERS,
            (
                "Real tech trigger validation failed: "
                f"steam_power={steam}, electronics={electronics}."
            ),
        )
        journal.event(
            "counterexample",
            "Technology-trigger stage rejected with furnace/trigger diagnostics.",
            diagnostics=trigger_diagnostics,
        )
        return False

    journal.state["engineering_progression"]["achieved"] = [
        "steam_power_trigger",
        "electronics_trigger",
    ]
    journal.complete_stage(
        STAGE_TECHNOLOGY_TRIGGERS,
        "Factorio 2.0 craft-item triggers unlocked Steam Power and Electronics.",
    )
    journal.event(
        "technology",
        "Steam Power and Electronics unlocked through real game triggers.",
        steam_power=steam,
        electronics=electronics,
    )
    return True


def _coal_survival(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        STAGE_COAL_COMMISSIONING,
        status="validating",
        detail=(
            "Commissioning the first coal extraction cell. Agent-mediated "
            "refuel is measured as intervention and is not autonomy."
        ),
        next_action="measure coal commissioning before electric transition",
    )
    commissioning_round_seconds = max(8, settle_seconds // 2)
    code = f"""
craft_item(Prototype.BurnerMiningDrill,quantity=1)
craft_item(Prototype.WoodenChest,quantity=1)
move_to(coal_pos)
coal_drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=coal_pos,
    direction=Direction.DOWN,
    exact=False,
)
coal_seed=min(2,inspect_inventory()[Prototype.Coal])
coal_drill=insert_item(Prototype.Coal,coal_drill,quantity=coal_seed)
coal_chest=place_entity_next_to(
    Prototype.WoodenChest,
    coal_drill.position,
    direction=Direction.DOWN,
)

# A single short sleep made this commissioning gate unnecessarily flaky.
# Observe the endogenous chest output over bounded intervals before deciding
# that the drill/output geometry failed.
coal_first_output=0
coal_wait_rounds=0
for coal_wait_round in range(1,5):
    sleep({commissioning_round_seconds})
    coal_wait_rounds=coal_wait_round
    coal_first_output=inspect_inventory(coal_chest)[Prototype.Coal]
    if coal_first_output>0:
        break

coal_transfer=0
if coal_first_output>0:
    coal_transfer=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(4,coal_first_output),
    )
if coal_transfer>0:
    coal_drill=insert_item(
        Prototype.Coal,
        coal_drill,
        quantity=min(2,coal_transfer),
    )

# Bootstrap coal becomes commissioning inventory only after the physical drill
# has demonstrated at least one endogenous output item.
remaining_player_coal=inspect_inventory()[Prototype.Coal]
if coal_first_output>0 and remaining_player_coal>0:
    coal_chest=insert_item(
        Prototype.Coal,
        coal_chest,
        quantity=remaining_player_coal,
    )

sleep({settle_seconds})
coal_drill=get_entity(
    Prototype.BurnerMiningDrill,
    coal_drill.position,
)
coal_stockpile=inspect_inventory(coal_chest)[Prototype.Coal]
coal_drill_status=str(coal_drill.status)
print({{
  'coal_seed':coal_seed,
  'coal_first_output':coal_first_output,
  'coal_wait_rounds':coal_wait_rounds,
  'coal_transfer':coal_transfer,
  'coal_stockpile':coal_stockpile,
  'coal_drill_status':coal_drill_status,
}})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "coal_first_output", 0) or 0) > 0
            and float(getattr(namespace, "coal_transfer", 0) or 0) > 0
            and float(getattr(namespace, "coal_stockpile", 0) or 0) > 0
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    diagnostics = {
        "accepted": step.accepted,
        "error_occurred": bool(step.info.get("error_occurred")),
        "result": str(step.info.get("result", ""))[:1800],
        "ticks": step.info.get("ticks"),
        "policy_execution_time": step.info.get("policy_execution_time"),
        "coal_seed": float(getattr(namespace, "coal_seed", 0) or 0),
        "first_output": float(
            getattr(namespace, "coal_first_output", 0) or 0
        ),
        "wait_rounds": int(
            getattr(namespace, "coal_wait_rounds", 0) or 0
        ),
        "manual_refuel_transfer": float(
            getattr(namespace, "coal_transfer", 0) or 0
        ),
        "stockpile": float(
            getattr(namespace, "coal_stockpile", 0) or 0
        ),
        "drill_status": str(
            getattr(namespace, "coal_drill_status", "")
        ),
        "round_seconds": commissioning_round_seconds,
    }
    journal.state["metrics"]["coal_commissioning_diagnostics"] = diagnostics

    if not step.accepted:
        journal.fail_stage(
            STAGE_COAL_COMMISSIONING,
            "Open-play coal cell failed endogenous survival.",
        )
        journal.event(
            "counterexample",
            "Coal commissioning failed to demonstrate endogenous drill output.",
            diagnostics=diagnostics,
        )
        return False

    journal.state["metrics"]["open_play_coal_stockpile"] = diagnostics[
        "stockpile"
    ]
    journal.complete_stage(
        STAGE_COAL_COMMISSIONING,
        "Coal commissioning produced an internal stockpile; refuel remained agent-mediated.",
    )
    return True

def _steam_power(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        STAGE_STEAM_COMMISSIONING,
        status="validating",
        detail=(
            "Commissioning a real Steam Power network. Manual boiler fuel is "
            "allowed only in commissioning and is tracked as intervention."
        ),
        next_action="demonstrate steam generation before physical fuel automation",
    )
    code = f"""
move_to(iron_furnace.position)
for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    extra_iron=inspect_inventory(furnace)[Prototype.IronPlate]
    if extra_iron>0:
        extract_item(
            Prototype.IronPlate,
            furnace,
            quantity=extra_iron,
        )
move_to(copper_furnace.position)
for furnace in (copper_furnace,copper_furnace_2):
    extra_copper=inspect_inventory(furnace)[Prototype.CopperPlate]
    if extra_copper>0:
        extract_item(
            Prototype.CopperPlate,
            furnace,
            quantity=extra_copper,
        )

move_to(coal_chest.position)
power_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
power_coal=0
if power_coal_available>0:
    power_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(10,power_coal_available),
    )

craft_item(Prototype.OffshorePump,quantity=1)
craft_item(Prototype.Boiler,quantity=1)
craft_item(Prototype.SteamEngine,quantity=1)
craft_item(Prototype.Pipe,quantity=24)

water_position=nearest(Resource.Water)
move_to(water_position)
offshore_pump=place_entity(
    Prototype.OffshorePump,
    position=water_position,
    exact=False,
)

boiler_box=BuildingBox(
    width=Prototype.Boiler.WIDTH+6,
    height=Prototype.Boiler.HEIGHT+6,
)
boiler_area=nearest_buildable(
    Prototype.Boiler,
    boiler_box,
    offshore_pump.position,
)
move_to(boiler_area.center)
boiler=place_entity(
    Prototype.Boiler,
    position=boiler_area.center,
    direction=Direction.LEFT,
)
if power_coal>0:
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=min(8,power_coal),
    )

engine_box=BuildingBox(
    width=Prototype.SteamEngine.WIDTH+6,
    height=Prototype.SteamEngine.HEIGHT+6,
)
engine_area=nearest_buildable(
    Prototype.SteamEngine,
    engine_box,
    boiler.position,
)
move_to(engine_area.center)
steam_engine=place_entity(
    Prototype.SteamEngine,
    position=engine_area.center,
    direction=Direction.LEFT,
)
connect_entities(offshore_pump,boiler,Prototype.Pipe)
connect_entities(boiler,steam_engine,Prototype.Pipe)
sleep({settle_seconds})
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
open_play_steam_energy=float(steam_engine.energy or 0)
print({{'steam_energy':open_play_steam_energy}})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "open_play_steam_energy", 0) or 0) > 0
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    if not step.accepted:
        journal.fail_stage(STAGE_STEAM_COMMISSIONING, "Open-play steam network generated no power.")
        return False
    journal.state["metrics"]["open_play_steam_energy"] = float(
        getattr(namespace, "open_play_steam_energy", 0) or 0
    )
    journal.complete_stage(
        STAGE_STEAM_COMMISSIONING,
        "Steam network demonstrated electrical generation during commissioning.",
    )
    return True


def _lab_and_automation(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    instance = env.unwrapped.instance
    namespace = instance.namespace

    lab_plan = EARLY_GAME_PRODUCTION_PLANNER.plan("lab", 1.0)
    science_plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
        "automation-science-pack",
        10.0,
    )
    required_iron = (
        float(lab_plan.raw_requirements_per_s.get("iron-plate", 0.0))
        + float(
            science_plan.raw_requirements_per_s.get("iron-plate", 0.0)
        )
    )
    required_copper = (
        float(lab_plan.raw_requirements_per_s.get("copper-plate", 0.0))
        + float(
            science_plan.raw_requirements_per_s.get("copper-plate", 0.0)
        )
    )
    material_safety_factor = 1.25
    iron_buffer_target = int(
        required_iron * material_safety_factor + 0.999
    )
    copper_buffer_target = int(
        required_copper * material_safety_factor + 0.999
    )

    journal.set_stage(
        STAGE_LAB_BOOTSTRAP,
        status="validating",
        detail=(
            "Building a DAG-sized plate buffer for one Lab plus 10 red "
            "science packs, then powering the lab and researching Automation."
        ),
        next_action="research Automation with a validated material buffer",
    )

    preflight_code = f"""
lab_buffer_target_iron={iron_buffer_target}
lab_buffer_target_copper={copper_buffer_target}
lab_buffer_rounds=0

move_to(iron_furnace.position)
for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    lab_extra_iron=inspect_inventory(furnace)[Prototype.IronPlate]
    if lab_extra_iron>0:
        extract_item(
            Prototype.IronPlate,
            furnace,
            quantity=lab_extra_iron,
        )
move_to(copper_furnace.position)
for furnace in (copper_furnace,copper_furnace_2):
    lab_extra_copper=inspect_inventory(furnace)[Prototype.CopperPlate]
    if lab_extra_copper>0:
        extract_item(
            Prototype.CopperPlate,
            furnace,
            quantity=lab_extra_copper,
        )

lab_buffer_inventory=inspect_inventory()
lab_buffer_iron=float(
    lab_buffer_inventory[Prototype.IronPlate]
)
lab_buffer_copper=float(
    lab_buffer_inventory[Prototype.CopperPlate]
)

for lab_buffer_round in range(1,7):
    if (
        lab_buffer_iron>=lab_buffer_target_iron
        and lab_buffer_copper>=lab_buffer_target_copper
    ):
        break

    sleep(20)

    move_to(iron_furnace.position)
    for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
        lab_extra_iron=inspect_inventory(furnace)[Prototype.IronPlate]
        if lab_extra_iron>0:
            extract_item(
                Prototype.IronPlate,
                furnace,
                quantity=lab_extra_iron,
            )
    move_to(copper_furnace.position)
    for furnace in (copper_furnace,copper_furnace_2):
        lab_extra_copper=inspect_inventory(furnace)[Prototype.CopperPlate]
        if lab_extra_copper>0:
            extract_item(
                Prototype.CopperPlate,
                furnace,
                quantity=lab_extra_copper,
            )

    lab_buffer_rounds=lab_buffer_round
    lab_buffer_inventory=inspect_inventory()
    lab_buffer_iron=float(
        lab_buffer_inventory[Prototype.IronPlate]
    )
    lab_buffer_copper=float(
        lab_buffer_inventory[Prototype.CopperPlate]
    )

print({{
  'iron':lab_buffer_iron,
  'copper':lab_buffer_copper,
  'iron_target':lab_buffer_target_iron,
  'copper_target':lab_buffer_target_copper,
  'rounds':lab_buffer_rounds,
}})
"""

    def accept_preflight(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        )

    preflight = executor.execute(
        preflight_code,
        accept=accept_preflight,
        use_checkpoint_for_action=False,
    )
    buffer_iron = float(
        getattr(namespace, "lab_buffer_iron", 0) or 0
    )
    buffer_copper = float(
        getattr(namespace, "lab_buffer_copper", 0) or 0
    )
    preflight_diagnostics = {
        "accepted": preflight.accepted,
        "error_occurred": bool(preflight.info.get("error_occurred")),
        "result": str(preflight.info.get("result", ""))[:1200],
        "ticks": preflight.info.get("ticks"),
        "policy_execution_time": preflight.info.get(
            "policy_execution_time"
        ),
        "iron_buffer": buffer_iron,
        "copper_buffer": buffer_copper,
        "iron_target": iron_buffer_target,
        "copper_target": copper_buffer_target,
        "rounds": int(
            getattr(namespace, "lab_buffer_rounds", 0) or 0
        ),
        "safety_factor": material_safety_factor,
        "dag_raw_requirements": {
            "lab": dict(lab_plan.raw_requirements_per_s),
            "red_science_10": dict(
                science_plan.raw_requirements_per_s
            ),
        },
    }
    journal.state["metrics"]["lab_material_preflight"] = (
        preflight_diagnostics
    )
    if (
        not preflight.accepted
        or buffer_iron < iron_buffer_target
        or buffer_copper < copper_buffer_target
    ):
        journal.fail_stage(
            STAGE_LAB_BOOTSTRAP,
            (
                "Lab material preflight failed: "
                f"iron={buffer_iron:.0f}/{iron_buffer_target}, "
                f"copper={buffer_copper:.0f}/{copper_buffer_target}."
            ),
        )
        journal.event(
            "counterexample",
            "Lab material preflight rejected before construction.",
            diagnostics=preflight_diagnostics,
        )
        return False

    code = f"""
# Provision enough endogenous fuel for the research window.
move_to(coal_chest.position)
lab_power_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
lab_power_coal=0
if lab_power_coal_available>0:
    lab_power_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(8,lab_power_coal_available),
    )
if lab_power_coal>0:
    move_to(boiler.position)
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=lab_power_coal,
    )

# Materialize the Lab DAG explicitly instead of relying on recursive
# autocrafting heuristics in FLE.
craft_item(Prototype.CopperCable,quantity=30)
craft_item(Prototype.ElectronicCircuit,quantity=10)
craft_item(Prototype.IronGearWheel,quantity=12)
craft_item(Prototype.TransportBelt,quantity=4)
craft_item(Prototype.Lab,quantity=1)
lab_trigger_inventory=inspect_inventory()[Prototype.Lab]
sleep(2)

# Red-science batch is also materialized explicitly.
craft_item(Prototype.IronGearWheel,quantity=10)
craft_item(Prototype.AutomationSciencePack,quantity=10)
red_packs=inspect_inventory()[Prototype.AutomationSciencePack]

lab_box=BuildingBox(width=7,height=7)
lab_area=nearest_buildable(Prototype.Lab,lab_box,steam_engine.position)
move_to(lab_area.center)
lab=place_entity(Prototype.Lab,position=lab_area.center)

pole_count=get_connection_amount(
    steam_engine,
    lab,
    connection_type=Prototype.SmallElectricPole,
)
pole_target=max(2,pole_count+1)
craft_item(Prototype.SmallElectricPole,quantity=pole_target)
pole_inventory=inspect_inventory()[Prototype.SmallElectricPole]
power_connection=connect_entities(
    steam_engine,
    lab,
    Prototype.SmallElectricPole,
)

lab=get_entity(Prototype.Lab,lab.position)
lab_energy_before=float(lab.energy or 0)
lab_status_before=str(lab.status)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
engine_status_before=str(steam_engine.status)

lab=insert_item(
    Prototype.AutomationSciencePack,
    lab,
    quantity=min(10,red_packs),
)
research_requirements=set_research(Technology.Automation)
sleep({settle_seconds})

lab=get_entity(Prototype.Lab,lab.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
boiler=get_entity(Prototype.Boiler,boiler.position)
lab_energy_after=float(lab.energy or 0)
lab_status_after=str(lab.status)
engine_status_after=str(steam_engine.status)
boiler_status_after=str(boiler.status)
lab_packs_after=inspect_inventory(lab)[Prototype.AutomationSciencePack]

automation_remaining=get_research_progress(Technology.Automation)
automation_remaining_count=0
for requirement in automation_remaining:
    automation_remaining_count+=requirement.count
automation_remaining_text=str(automation_remaining)

print({{
  'lab_trigger_inventory':lab_trigger_inventory,
  'red_packs':red_packs,
  'pole_count':pole_count,
  'pole_target':pole_target,
  'lab_energy_before':lab_energy_before,
  'lab_energy_after':lab_energy_after,
  'lab_status_before':lab_status_before,
  'lab_status_after':lab_status_after,
  'engine_status_before':engine_status_before,
  'engine_status_after':engine_status_after,
  'boiler_status_after':boiler_status_after,
  'lab_packs_after':lab_packs_after,
  'remaining_count':automation_remaining_count,
  'remaining':automation_remaining_text,
}})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    science_unlocked = _technology_researched(
        instance,
        "automation-science-pack",
    )
    automation = _technology_researched(instance, "automation")
    research_rounds: list[dict[str, Any]] = []
    for round_index in range(1, 4):
        if automation or not step.accepted:
            break
        round_step = executor.execute(
            "sleep(60)\n"
            "lab=get_entity(Prototype.Lab,lab.position)\n"
            "lab_energy_after=float(lab.energy or 0)\n"
            "lab_status_after=str(lab.status)\n"
            "lab_packs_after=inspect_inventory(lab)[Prototype.AutomationSciencePack]\n"
            "automation_remaining=get_research_progress(Technology.Automation)\n"
            "automation_remaining_count=0\n"
            "for requirement in automation_remaining:\n"
            "    automation_remaining_count+=requirement.count\n"
            "automation_remaining_text=str(automation_remaining)\n",
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        automation = _technology_researched(instance, "automation")
        research_rounds.append(
            {
                "round": round_index,
                "accepted": round_step.accepted,
                "automation_researched": automation,
                "remaining_count": float(
                    getattr(namespace, "automation_remaining_count", 0) or 0
                ),
                "lab_status": str(
                    getattr(namespace, "lab_status_after", "")
                ),
                "lab_energy": float(
                    getattr(namespace, "lab_energy_after", 0) or 0
                ),
                "error_occurred": bool(
                    round_step.info.get("error_occurred")
                ),
            }
        )
        if not round_step.accepted:
            break

    lab_diagnostics = {
        "accepted": step.accepted,
        "error_occurred": bool(step.info.get("error_occurred")),
        "result": str(step.info.get("result", ""))[:1600],
        "ticks": step.info.get("ticks"),
        "policy_execution_time": step.info.get("policy_execution_time"),
        "science_unlocked": science_unlocked,
        "automation_researched": automation,
        "red_packs_crafted": float(
            getattr(namespace, "red_packs", 0) or 0
        ),
        "pole_count": float(getattr(namespace, "pole_count", 0) or 0),
        "pole_target": float(getattr(namespace, "pole_target", 0) or 0),
        "lab_power_coal": float(
            getattr(namespace, "lab_power_coal", 0) or 0
        ),
        "lab_energy_before": float(
            getattr(namespace, "lab_energy_before", 0) or 0
        ),
        "lab_energy_after": float(
            getattr(namespace, "lab_energy_after", 0) or 0
        ),
        "lab_status_before": str(
            getattr(namespace, "lab_status_before", "")
        ),
        "lab_status_after": str(
            getattr(namespace, "lab_status_after", "")
        ),
        "engine_status_before": str(
            getattr(namespace, "engine_status_before", "")
        ),
        "engine_status_after": str(
            getattr(namespace, "engine_status_after", "")
        ),
        "boiler_status_after": str(
            getattr(namespace, "boiler_status_after", "")
        ),
        "lab_packs_after": float(
            getattr(namespace, "lab_packs_after", 0) or 0
        ),
        "automation_remaining_count": float(
            getattr(namespace, "automation_remaining_count", 0) or 0
        ),
        "automation_remaining": str(
            getattr(namespace, "automation_remaining_text", "")
        ),
        "material_preflight": preflight_diagnostics,
        "research_rounds": research_rounds,
    }
    journal.state["metrics"].update(
        {
            "automation_science_triggered": science_unlocked,
            "automation_researched": automation,
            "open_play_red_packs_crafted": lab_diagnostics[
                "red_packs_crafted"
            ],
            "lab_automation_diagnostics": lab_diagnostics,
        }
    )
    if not step.accepted or not science_unlocked or not automation:
        journal.fail_stage(
            STAGE_LAB_BOOTSTRAP,
            (
                "Open-play lab validation failed: "
                f"science_unlock={science_unlocked}, automation={automation}."
            ),
        )
        journal.event(
            "counterexample",
            "Lab/Automation gate rejected with power, science and progress diagnostics.",
            diagnostics=lab_diagnostics,
        )
        return False
    journal.complete_stage(
        STAGE_LAB_BOOTSTRAP,
        "Lab trigger unlocked red science and 10 packs researched Automation.",
    )
    journal.event(
        "technology",
        "Automation researched in a powered lab using earned red science.",
        material_preflight=preflight_diagnostics,
    )
    return True


def _powered_red_science(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace

    assembler_plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
        "assembling-machine-1",
        1.0,
    )
    red_plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
        "automation-science-pack",
        8.0,
    )
    pole_plan = EARLY_GAME_PRODUCTION_PLANNER.plan(
        "small-electric-pole",
        4.0,
    )
    combined_raw: dict[str, float] = {}
    for plan in (assembler_plan, red_plan, pole_plan):
        for item, value in plan.raw_requirements_per_s.items():
            combined_raw[item] = combined_raw.get(item, 0.0) + float(value)

    powered_red_safety_factor = 1.25
    powered_red_iron_target = int(
        combined_raw.get("iron-plate", 0.0)
        * powered_red_safety_factor
        + 0.999
    )
    powered_red_copper_target = int(
        combined_raw.get("copper-plate", 0.0)
        * powered_red_safety_factor
        + 0.999
    )
    powered_red_wood_target = int(
        combined_raw.get("wood", 0.0)
        * powered_red_safety_factor
        + 0.999
    )

    journal.set_stage(
        STAGE_POWERED_RED_SCIENCE,
        status="validating",
        detail=(
            "Using the newly unlocked assembling machine to produce red science "
            "under real open-play power."
        ),
        next_action="validate powered automation-science production",
    )
    preflight_code = f"""
powered_red_iron_target={powered_red_iron_target}
powered_red_copper_target={powered_red_copper_target}
powered_red_wood_target={powered_red_wood_target}
powered_red_rounds=0

def collect_powered_red_plates():
    move_to(iron_furnace.position)
    for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
        count=inspect_inventory(furnace)[Prototype.IronPlate]
        if count>0:
            extract_item(Prototype.IronPlate,furnace,quantity=count)
    move_to(copper_furnace.position)
    for furnace in (copper_furnace,copper_furnace_2):
        count=inspect_inventory(furnace)[Prototype.CopperPlate]
        if count>0:
            extract_item(Prototype.CopperPlate,furnace,quantity=count)

collect_powered_red_plates()

for powered_red_round in range(1,7):
    inv=inspect_inventory()
    powered_red_iron=float(inv[Prototype.IronPlate])
    powered_red_copper=float(inv[Prototype.CopperPlate])
    powered_red_wood=float(inv[Prototype.Wood])
    if (
        powered_red_iron>=powered_red_iron_target
        and powered_red_copper>=powered_red_copper_target
        and powered_red_wood>=powered_red_wood_target
    ):
        break

    move_to(coal_chest.position)
    reserve=inspect_inventory(coal_chest)[Prototype.Coal]
    if reserve>0:
        extract_item(
            Prototype.Coal,
            coal_chest,
            quantity=min(18,reserve),
        )

    move_to(iron_furnace.position)
    for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
        remaining=inspect_inventory()[Prototype.IronOre]
        if remaining>0:
            furnace=insert_item(
                Prototype.IronOre,
                furnace,
                quantity=min(50,remaining),
            )
        fuel=inspect_inventory()[Prototype.Coal]
        if fuel>0:
            furnace=insert_item(
                Prototype.Coal,
                furnace,
                quantity=min(5,fuel),
            )

    move_to(copper_furnace.position)
    for furnace in (copper_furnace,copper_furnace_2):
        remaining=inspect_inventory()[Prototype.CopperOre]
        if remaining>0:
            furnace=insert_item(
                Prototype.CopperOre,
                furnace,
                quantity=min(50,remaining),
            )
        fuel=inspect_inventory()[Prototype.Coal]
        if fuel>0:
            furnace=insert_item(
                Prototype.Coal,
                furnace,
                quantity=min(5,fuel),
            )

    sleep(20)
    collect_powered_red_plates()
    powered_red_rounds=powered_red_round

inv=inspect_inventory()
powered_red_iron=float(inv[Prototype.IronPlate])
powered_red_copper=float(inv[Prototype.CopperPlate])
powered_red_wood=float(inv[Prototype.Wood])
print({{
  'iron':powered_red_iron,
  'copper':powered_red_copper,
  'wood':powered_red_wood,
  'iron_target':powered_red_iron_target,
  'copper_target':powered_red_copper_target,
  'wood_target':powered_red_wood_target,
  'rounds':powered_red_rounds,
}})
"""

    preflight_step = executor.execute(
        preflight_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
    )
    powered_red_iron = float(
        getattr(namespace, "powered_red_iron", 0) or 0
    )
    powered_red_copper = float(
        getattr(namespace, "powered_red_copper", 0) or 0
    )
    powered_red_wood = float(
        getattr(namespace, "powered_red_wood", 0) or 0
    )
    preflight_diagnostics = {
        "accepted": preflight_step.accepted,
        "error_occurred": bool(
            preflight_step.info.get("error_occurred")
        ),
        "result": str(preflight_step.info.get("result", ""))[:1600],
        "ticks": preflight_step.info.get("ticks"),
        "policy_execution_time": preflight_step.info.get(
            "policy_execution_time"
        ),
        "iron_buffer": powered_red_iron,
        "copper_buffer": powered_red_copper,
        "wood_buffer": powered_red_wood,
        "iron_target": powered_red_iron_target,
        "copper_target": powered_red_copper_target,
        "wood_target": powered_red_wood_target,
        "rounds": int(
            getattr(namespace, "powered_red_rounds", 0) or 0
        ),
        "safety_factor": powered_red_safety_factor,
        "dag_raw_requirements": combined_raw,
    }
    journal.state["metrics"]["powered_red_material_preflight"] = (
        preflight_diagnostics
    )
    if (
        not preflight_step.accepted
        or powered_red_iron < powered_red_iron_target
        or powered_red_copper < powered_red_copper_target
        or powered_red_wood < powered_red_wood_target
    ):
        journal.fail_stage(
            STAGE_POWERED_RED_SCIENCE,
            (
                "Powered red-science material preflight failed: "
                f"iron={powered_red_iron:.0f}/{powered_red_iron_target}, "
                f"copper={powered_red_copper:.0f}/{powered_red_copper_target}, "
                f"wood={powered_red_wood:.0f}/{powered_red_wood_target}."
            ),
        )
        journal.event(
            "counterexample",
            "Powered red-science material preflight rejected.",
            diagnostics=preflight_diagnostics,
        )
        return False

    code = f"""
# Recommission boiler fuel after the Automation research window.
move_to(coal_chest.position)
red_science_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
red_science_coal=0
if red_science_coal_available>0:
    red_science_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(20,red_science_coal_available),
    )
if red_science_coal>0:
    move_to(boiler.position)
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=red_science_coal,
    )

move_to(iron_furnace.position)
for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    assembler_extra_iron=inspect_inventory(furnace)[Prototype.IronPlate]
    if assembler_extra_iron>0:
        extract_item(
            Prototype.IronPlate,
            furnace,
            quantity=assembler_extra_iron,
        )
move_to(copper_furnace.position)
for furnace in (copper_furnace,copper_furnace_2):
    assembler_extra_copper=inspect_inventory(furnace)[Prototype.CopperPlate]
    if assembler_extra_copper>0:
        extract_item(
            Prototype.CopperPlate,
            furnace,
            quantity=assembler_extra_copper,
        )

# Explicit DAG materialization: 3 circuits + 13 gears gives
# 5 gears to the assembler recipe and leaves 8 for red-science feed.
craft_item(Prototype.CopperCable,quantity=9)
craft_item(Prototype.ElectronicCircuit,quantity=3)
craft_item(Prototype.IronGearWheel,quantity=13)
craft_item(Prototype.AssemblingMachine1,quantity=1)

assembler_box=BuildingBox(
    width=Prototype.AssemblingMachine1.WIDTH+6,
    height=Prototype.AssemblingMachine1.HEIGHT+6,
)
assembler_area=nearest_buildable(
    Prototype.AssemblingMachine1,
    assembler_box,
    lab.position,
)
move_to(assembler_area.center)
science_assembler=place_entity(
    Prototype.AssemblingMachine1,
    position=assembler_area.center,
)
science_assembler=set_entity_recipe(
    science_assembler,
    Prototype.AutomationSciencePack,
)
red_pole_count=get_connection_amount(
    steam_engine,
    science_assembler,
    connection_type=Prototype.SmallElectricPole,
)
red_pole_target=max(1,red_pole_count+1)
craft_item(
    Prototype.SmallElectricPole,
    quantity=red_pole_target,
)
connect_entities(
    steam_engine,
    science_assembler,
    Prototype.SmallElectricPole,
)

gear_input=min(8,inspect_inventory()[Prototype.IronGearWheel])
copper_input=min(8,inspect_inventory()[Prototype.CopperPlate])
if gear_input>0:
    science_assembler=insert_item(
        Prototype.IronGearWheel,
        science_assembler,
        quantity=gear_input,
    )
if copper_input>0:
    science_assembler=insert_item(
        Prototype.CopperPlate,
        science_assembler,
        quantity=copper_input,
    )
science_assembler=get_entity(
    Prototype.AssemblingMachine1,
    science_assembler.position,
)
red_assembler_status_before=str(science_assembler.status)
red_assembler_energy_before=float(science_assembler.energy or 0)
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
red_boiler_status_before=str(boiler.status)
red_engine_status_before=str(steam_engine.status)

sleep({settle_seconds})

science_assembler=get_entity(
    Prototype.AssemblingMachine1,
    science_assembler.position,
)
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
automated_red=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
red_assembler_status_after=str(science_assembler.status)
red_assembler_energy_after=float(science_assembler.energy or 0)
red_boiler_status_after=str(boiler.status)
red_engine_status_after=str(steam_engine.status)
print({{
  'gear_input':gear_input,
  'copper_input':copper_input,
  'automated_red':automated_red,
  'coal_added':red_science_coal,
  'assembler_status_before':red_assembler_status_before,
  'assembler_status_after':red_assembler_status_after,
  'assembler_energy_before':red_assembler_energy_before,
  'assembler_energy_after':red_assembler_energy_after,
  'boiler_status_before':red_boiler_status_before,
  'boiler_status_after':red_boiler_status_after,
  'engine_status_before':red_engine_status_before,
  'engine_status_after':red_engine_status_after,
}})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "automated_red", 0) or 0) > 0
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    output = float(getattr(namespace, "automated_red", 0) or 0)
    diagnostics = {
        "accepted": step.accepted,
        "error_occurred": bool(step.info.get("error_occurred")),
        "result": str(step.info.get("result", ""))[:1800],
        "ticks": step.info.get("ticks"),
        "policy_execution_time": step.info.get("policy_execution_time"),
        "coal_added": float(
            getattr(namespace, "red_science_coal", 0) or 0
        ),
        "gear_input": float(getattr(namespace, "gear_input", 0) or 0),
        "copper_input": float(
            getattr(namespace, "copper_input", 0) or 0
        ),
        "output": output,
        "pole_count": float(
            getattr(namespace, "red_pole_count", 0) or 0
        ),
        "pole_target": float(
            getattr(namespace, "red_pole_target", 0) or 0
        ),
        "material_preflight": preflight_diagnostics,
        "assembler_status_before": str(
            getattr(namespace, "red_assembler_status_before", "")
        ),
        "assembler_status_after": str(
            getattr(namespace, "red_assembler_status_after", "")
        ),
        "assembler_energy_before": float(
            getattr(namespace, "red_assembler_energy_before", 0) or 0
        ),
        "assembler_energy_after": float(
            getattr(namespace, "red_assembler_energy_after", 0) or 0
        ),
        "boiler_status_before": str(
            getattr(namespace, "red_boiler_status_before", "")
        ),
        "boiler_status_after": str(
            getattr(namespace, "red_boiler_status_after", "")
        ),
        "engine_status_before": str(
            getattr(namespace, "red_engine_status_before", "")
        ),
        "engine_status_after": str(
            getattr(namespace, "red_engine_status_after", "")
        ),
    }
    journal.state["metrics"]["powered_red_science_diagnostics"] = diagnostics
    if not step.accepted:
        journal.fail_stage(
            STAGE_POWERED_RED_SCIENCE,
            "Assembling machine produced no open-play automation science.",
        )
        journal.event(
            "counterexample",
            "Powered red-science gate rejected with energy/input diagnostics.",
            diagnostics=diagnostics,
        )
        return False
    journal.state["metrics"]["open_play_automated_red_science"] = output
    journal.complete_stage(
        STAGE_POWERED_RED_SCIENCE,
        f"Powered assembler produced {output:.0f} automation science packs.",
    )
    return True


def _unlock_logistic_science(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    instance = env.unwrapped.instance
    namespace = instance.namespace
    required_red = 75
    production_round_seconds = 120
    production_round_limit = 7
    research_round_seconds = 120
    research_round_limit = 4

    journal.set_stage(
        STAGE_LOGISTIC_SCIENCE_UNLOCK,
        status="validating",
        detail=(
            "Producing 75 automation science packs through bounded 120 s "
            "checkpoints, then researching Logistic Science Pack through "
            "bounded research checkpoints."
        ),
        next_action="research Logistic Science Pack with powered red science",
    )

    preparation_code = f"""
scale_required_red={required_red}
scale_required_iron=scale_required_red*2
scale_required_copper=scale_required_red
scale_material_rounds=0

# The electric transition retires the auxiliary furnaces and exposes the two
# permanent autonomous output chests. Downstream science must consume material
# from that validated backbone instead of assuming residual commissioning WIP.
for scale_material_round in range(1,7):
    move_to(iron_output_chest.position)
    count=inspect_inventory(iron_output_chest)[Prototype.IronPlate]
    if count>0:
        extract_item(
            Prototype.IronPlate,
            iron_output_chest,
            quantity=count,
        )
    move_to(copper_output_chest.position)
    count=inspect_inventory(copper_output_chest)[Prototype.CopperPlate]
    if count>0:
        extract_item(
            Prototype.CopperPlate,
            copper_output_chest,
            quantity=count,
        )

    # Catch any plate produced between the furnace and its output inserter.
    move_to(iron_furnace.position)
    count=inspect_inventory(iron_furnace)[Prototype.IronPlate]
    if count>0:
        extract_item(Prototype.IronPlate,iron_furnace,quantity=count)
    move_to(copper_furnace.position)
    count=inspect_inventory(copper_furnace)[Prototype.CopperPlate]
    if count>0:
        extract_item(Prototype.CopperPlate,copper_furnace,quantity=count)

    scale_inventory=inspect_inventory()
    scale_iron=float(scale_inventory[Prototype.IronPlate])
    scale_copper=float(scale_inventory[Prototype.CopperPlate])
    scale_material_rounds=scale_material_round
    if (
        scale_iron>=scale_required_iron
        and scale_copper>=scale_required_copper
    ):
        break
    sleep(120)

scale_inventory=inspect_inventory()
scale_iron=float(scale_inventory[Prototype.IronPlate])
scale_copper=float(scale_inventory[Prototype.CopperPlate])

scale_gears=0
if (
    scale_iron>=scale_required_iron
    and scale_copper>=scale_required_copper
):
    craft_item(
        Prototype.IronGearWheel,
        quantity=scale_required_red,
    )
    scale_gears=inspect_inventory()[Prototype.IronGearWheel]

scale_feed_gears=min(
    scale_required_red,
    scale_gears,
)
scale_feed_copper=min(
    scale_required_red,
    inspect_inventory()[Prototype.CopperPlate],
)

if scale_feed_gears>0:
    science_assembler=insert_item(
        Prototype.IronGearWheel,
        science_assembler,
        quantity=scale_feed_gears,
    )
if scale_feed_copper>0:
    science_assembler=insert_item(
        Prototype.CopperPlate,
        science_assembler,
        quantity=scale_feed_copper,
    )

# Production energy budget. This is explicitly commissioning debt and is
# counted by the intervention telemetry.
move_to(coal_chest.position)
scale_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
scale_coal=0
if scale_coal_available>0:
    scale_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(40,scale_coal_available),
    )
if scale_coal>0:
    move_to(boiler.position)
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=scale_coal,
    )

science_assembler=get_entity(
    Prototype.AssemblingMachine1,
    science_assembler.position,
)
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)

scale_output_initial=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
scale_assembler_status_prepared=str(science_assembler.status)
scale_assembler_energy_prepared=float(science_assembler.energy or 0)
scale_boiler_status_prepared=str(boiler.status)
scale_engine_status_prepared=str(steam_engine.status)

print({{
    'iron':scale_iron,
    'copper':scale_copper,
    'gears':scale_gears,
    'feed_gears':scale_feed_gears,
    'feed_copper':scale_feed_copper,
    'coal':scale_coal,
    'output_initial':scale_output_initial,
    'assembler_status':scale_assembler_status_prepared,
    'assembler_energy':scale_assembler_energy_prepared,
    'boiler_status':scale_boiler_status_prepared,
    'engine_status':scale_engine_status_prepared,
}})
"""

    preparation = executor.execute(
        preparation_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(
                getattr(namespace, "scale_feed_gears", 0) or 0
            )
            >= required_red
            and float(
                getattr(namespace, "scale_feed_copper", 0) or 0
            )
            >= required_red
        ),
        use_checkpoint_for_action=False,
    )
    preparation_diagnostics = {
        "accepted": preparation.accepted,
        "error_occurred": bool(
            preparation.info.get("error_occurred")
        ),
        "result": str(preparation.info.get("result", ""))[:2200],
        "policy_execution_time": preparation.info.get(
            "policy_execution_time"
        ),
        "ticks": preparation.info.get("ticks"),
        "iron_buffer": float(
            getattr(namespace, "scale_iron", 0) or 0
        ),
        "copper_buffer": float(
            getattr(namespace, "scale_copper", 0) or 0
        ),
        "material_rounds": int(
            getattr(namespace, "scale_material_rounds", 0) or 0
        ),
        "material_targets": {
            "iron_plate": required_red * 2,
            "copper_plate": required_red,
        },
        "gears": float(
            getattr(namespace, "scale_gears", 0) or 0
        ),
        "feed_gears": float(
            getattr(namespace, "scale_feed_gears", 0) or 0
        ),
        "feed_copper": float(
            getattr(namespace, "scale_feed_copper", 0) or 0
        ),
        "coal_added": float(
            getattr(namespace, "scale_coal", 0) or 0
        ),
        "assembler_status": str(
            getattr(
                namespace,
                "scale_assembler_status_prepared",
                "",
            )
        ),
        "assembler_energy": float(
            getattr(
                namespace,
                "scale_assembler_energy_prepared",
                0,
            )
            or 0
        ),
        "boiler_status": str(
            getattr(namespace, "scale_boiler_status_prepared", "")
        ),
        "engine_status": str(
            getattr(namespace, "scale_engine_status_prepared", "")
        ),
    }
    journal.state["metrics"][
        "logistic_science_preparation"
    ] = preparation_diagnostics
    if not preparation.accepted:
        journal.fail_stage(
            STAGE_LOGISTIC_SCIENCE_UNLOCK,
            "Logistic-science material/power preparation failed.",
        )
        journal.event(
            "counterexample",
            "Logistic-science preparation rejected.",
            diagnostics=preparation_diagnostics,
        )
        return False

    production_rounds: list[dict[str, Any]] = []
    scaled_red = float(
        getattr(namespace, "scale_output_initial", 0) or 0
    )
    for round_index in range(1, production_round_limit + 1):
        if scaled_red >= required_red:
            break
        round_code = f"""
sleep({production_round_seconds})
science_assembler=get_entity(
    Prototype.AssemblingMachine1,
    science_assembler.position,
)
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
scale_round_output=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
scale_round_assembler_status=str(science_assembler.status)
scale_round_assembler_energy=float(science_assembler.energy or 0)
scale_round_boiler_status=str(boiler.status)
scale_round_engine_status=str(steam_engine.status)
print({{
    'output':scale_round_output,
    'assembler_status':scale_round_assembler_status,
    'assembler_energy':scale_round_assembler_energy,
    'boiler_status':scale_round_boiler_status,
    'engine_status':scale_round_engine_status,
}})
"""
        round_step = executor.execute(
            round_code,
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        scaled_red = float(
            getattr(namespace, "scale_round_output", 0) or 0
        )
        row = {
            "round": round_index,
            "accepted": round_step.accepted,
            "error_occurred": bool(
                round_step.info.get("error_occurred")
            ),
            "result": str(
                round_step.info.get("result", "")
            )[:1200],
            "policy_execution_time": round_step.info.get(
                "policy_execution_time"
            ),
            "ticks": round_step.info.get("ticks"),
            "output": scaled_red,
            "assembler_status": str(
                getattr(
                    namespace,
                    "scale_round_assembler_status",
                    "",
                )
            ),
            "assembler_energy": float(
                getattr(
                    namespace,
                    "scale_round_assembler_energy",
                    0,
                )
                or 0
            ),
            "boiler_status": str(
                getattr(
                    namespace,
                    "scale_round_boiler_status",
                    "",
                )
            ),
            "engine_status": str(
                getattr(
                    namespace,
                    "scale_round_engine_status",
                    "",
                )
            ),
        }
        production_rounds.append(row)
        journal.state["metrics"][
            "logistic_science_production_rounds"
        ] = production_rounds
        journal.state["metrics"][
            "open_play_scaled_red_science"
        ] = scaled_red
        journal.flush()
        if not round_step.accepted:
            journal.fail_stage(
                STAGE_LOGISTIC_SCIENCE_UNLOCK,
                "Logistic-science bounded production checkpoint failed.",
            )
            journal.event(
                "counterexample",
                "Bounded red-science production checkpoint rejected.",
                diagnostics=row,
            )
            return False

    if scaled_red < required_red:
        diagnostics = {
            "phase": "production",
            "required_red": required_red,
            "scaled_red": scaled_red,
            "rounds": production_rounds,
        }
        journal.state["metrics"][
            "logistic_science_unlock_diagnostics"
        ] = diagnostics
        journal.fail_stage(
            STAGE_LOGISTIC_SCIENCE_UNLOCK,
            (
                "Powered red-science batch remained below research "
                f"requirement: {scaled_red:.0f}/{required_red}."
            ),
        )
        journal.event(
            "counterexample",
            "Red-science batch did not reach the technology requirement.",
            diagnostics=diagnostics,
        )
        return False

    research_preparation_code = f"""
science_assembler=get_entity(
    Prototype.AssemblingMachine1,
    science_assembler.position,
)
scaled_red=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
red_for_unlock=0
if scaled_red>0:
    move_to(science_assembler.position)
    red_for_unlock=extract_item(
        Prototype.AutomationSciencePack,
        science_assembler,
        quantity=min({required_red},scaled_red),
    )

# Research is a second long electrical load.
move_to(coal_chest.position)
scale_research_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
scale_research_coal=0
if scale_research_coal_available>0:
    scale_research_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(20,scale_research_coal_available),
    )
if scale_research_coal>0:
    move_to(boiler.position)
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=scale_research_coal,
    )

if red_for_unlock>0:
    move_to(lab.position)
    lab=insert_item(
        Prototype.AutomationSciencePack,
        lab,
        quantity=red_for_unlock,
    )
set_research(Technology.LogisticsSciencePack)
scale_research_remaining=str(
    get_research_progress(Technology.LogisticsSciencePack)
)
print({{
    'red_for_unlock':red_for_unlock,
    'research_coal':scale_research_coal,
    'remaining':scale_research_remaining,
}})
"""
    research_preparation = executor.execute(
        research_preparation_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(
                getattr(namespace, "red_for_unlock", 0) or 0
            )
            >= required_red
        ),
        use_checkpoint_for_action=False,
    )
    if not research_preparation.accepted:
        diagnostics = {
            "phase": "research_preparation",
            "accepted": False,
            "error_occurred": bool(
                research_preparation.info.get("error_occurred")
            ),
            "result": str(
                research_preparation.info.get("result", "")
            )[:1800],
            "red_for_unlock": float(
                getattr(namespace, "red_for_unlock", 0) or 0
            ),
        }
        journal.state["metrics"][
            "logistic_science_unlock_diagnostics"
        ] = diagnostics
        journal.fail_stage(
            STAGE_LOGISTIC_SCIENCE_UNLOCK,
            "Logistic-science research preparation failed.",
        )
        journal.event(
            "counterexample",
            "Logistic-science research preparation rejected.",
            diagnostics=diagnostics,
        )
        return False

    research_rounds: list[dict[str, Any]] = []
    researched = _technology_researched(
        instance,
        "logistic-science-pack",
    )
    for round_index in range(1, research_round_limit + 1):
        if researched:
            break
        round_code = f"""
sleep({research_round_seconds})
scale_research_remaining=str(
    get_research_progress(Technology.LogisticsSciencePack)
)
# Do not refresh Lab through FLE get_entity here. FLE 0.4.3 can fail
# deserializing a lab while its science inventory is populated. Research
# progress is authoritative; boiler/engine telemetry provides the power signal.
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
scale_research_boiler_status=str(boiler.status)
scale_research_engine_status=str(steam_engine.status)
print({{
    'remaining':scale_research_remaining,
    'boiler_status':scale_research_boiler_status,
    'engine_status':scale_research_engine_status,
}})
"""
        round_step = executor.execute(
            round_code,
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        researched = _technology_researched(
            instance,
            "logistic-science-pack",
        )
        row = {
            "round": round_index,
            "accepted": round_step.accepted,
            "error_occurred": bool(
                round_step.info.get("error_occurred")
            ),
            "result": str(
                round_step.info.get("result", "")
            )[:1200],
            "policy_execution_time": round_step.info.get(
                "policy_execution_time"
            ),
            "ticks": round_step.info.get("ticks"),
            "researched": researched,
            "remaining": str(
                getattr(
                    namespace,
                    "scale_research_remaining",
                    "",
                )
            ),
            "boiler_status": str(
                getattr(
                    namespace,
                    "scale_research_boiler_status",
                    "",
                )
            ),
            "engine_status": str(
                getattr(
                    namespace,
                    "scale_research_engine_status",
                    "",
                )
            ),
        }
        research_rounds.append(row)
        journal.state["metrics"][
            "logistic_science_research_rounds"
        ] = research_rounds
        journal.flush()
        if not round_step.accepted:
            break

    diagnostics = {
        "accepted": researched,
        "required_red": required_red,
        "scaled_red": scaled_red,
        "red_for_unlock": float(
            getattr(namespace, "red_for_unlock", 0) or 0
        ),
        "production_rounds": production_rounds,
        "research_rounds": research_rounds,
        "preparation": preparation_diagnostics,
        "research_coal_added": float(
            getattr(namespace, "scale_research_coal", 0) or 0
        ),
        "researched": researched,
    }
    journal.state["metrics"].update(
        {
            "open_play_scaled_red_science": scaled_red,
            "logistic_science_technology_researched": researched,
            "logistic_science_unlock_diagnostics": diagnostics,
        }
    )

    if not researched:
        journal.fail_stage(
            STAGE_LOGISTIC_SCIENCE_UNLOCK,
            (
                "Bounded Logistic Science Pack research did not finish "
                f"after {len(research_rounds)} checkpoint(s)."
            ),
        )
        journal.event(
            "counterexample",
            "Logistic-science technology remained incomplete.",
            diagnostics=diagnostics,
        )
        return False

    journal.complete_stage(
        STAGE_LOGISTIC_SCIENCE_UNLOCK,
        (
            "Logistic Science Pack technology researched from "
            f"{scaled_red:.0f} powered red packs."
        ),
    )
    return True


def _green_science_industry(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        STAGE_GREEN_SCIENCE_INDUSTRY,
        status="validating",
        detail=(
            "Building a powered cable/circuit/belt/inserter production DAG "
            "and consuming its output in green science."
        ),
        next_action="produce logistic science from powered intermediates",
    )

    green_raw: dict[str, float] = {}
    for target_item, amount in (
        ("assembling-machine-1", 5.0),
        ("small-electric-pole", 8.0),
        ("logistic-science-pack", 10.0),
    ):
        plan = EARLY_GAME_PRODUCTION_PLANNER.plan(target_item, amount)
        for item, value in plan.raw_requirements_per_s.items():
            green_raw[item] = green_raw.get(item, 0.0) + float(value)

    green_iron_target = max(
        1,
        int(float(green_raw.get("iron-plate", 0.0)) * 1.10 + 0.999),
    )
    green_copper_target = max(
        1,
        int(float(green_raw.get("copper-plate", 0.0)) * 1.10 + 0.999),
    )
    green_wood_target = max(
        1,
        int(float(green_raw.get("wood", 0.0)) * 1.10 + 0.999),
    )

    green_preflight_code = f"""
green_material_rounds=0
for green_material_round in range(1,9):
    move_to(iron_output_chest.position)
    count=inspect_inventory(iron_output_chest)[Prototype.IronPlate]
    if count>0:
        extract_item(
            Prototype.IronPlate,
            iron_output_chest,
            quantity=count,
        )
    move_to(copper_output_chest.position)
    count=inspect_inventory(copper_output_chest)[Prototype.CopperPlate]
    if count>0:
        extract_item(
            Prototype.CopperPlate,
            copper_output_chest,
            quantity=count,
        )

    green_inventory=inspect_inventory()
    green_buffer_iron=float(green_inventory[Prototype.IronPlate])
    green_buffer_copper=float(green_inventory[Prototype.CopperPlate])
    green_buffer_wood=float(green_inventory[Prototype.Wood])
    green_material_rounds=green_material_round
    if (
        green_buffer_iron>={green_iron_target}
        and green_buffer_copper>={green_copper_target}
        and green_buffer_wood>={green_wood_target}
    ):
        break
    sleep(120)

green_inventory=inspect_inventory()
green_buffer_iron=float(green_inventory[Prototype.IronPlate])
green_buffer_copper=float(green_inventory[Prototype.CopperPlate])
green_buffer_wood=float(green_inventory[Prototype.Wood])
print({{
    'iron':green_buffer_iron,
    'copper':green_buffer_copper,
    'wood':green_buffer_wood,
    'iron_target':{green_iron_target},
    'copper_target':{green_copper_target},
    'wood_target':{green_wood_target},
    'rounds':green_material_rounds,
}})
"""
    green_preflight = executor.execute(
        green_preflight_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(
                getattr(namespace, "green_buffer_iron", 0) or 0
            )
            >= green_iron_target
            and float(
                getattr(namespace, "green_buffer_copper", 0) or 0
            )
            >= green_copper_target
            and float(
                getattr(namespace, "green_buffer_wood", 0) or 0
            )
            >= green_wood_target
        ),
        use_checkpoint_for_action=False,
    )
    green_preflight_diagnostics = {
        "accepted": green_preflight.accepted,
        "error_occurred": bool(
            green_preflight.info.get("error_occurred")
        ),
        "result": str(green_preflight.info.get("result", ""))[:1800],
        "policy_execution_time": green_preflight.info.get(
            "policy_execution_time"
        ),
        "ticks": green_preflight.info.get("ticks"),
        "iron_buffer": float(
            getattr(namespace, "green_buffer_iron", 0) or 0
        ),
        "copper_buffer": float(
            getattr(namespace, "green_buffer_copper", 0) or 0
        ),
        "wood_buffer": float(
            getattr(namespace, "green_buffer_wood", 0) or 0
        ),
        "iron_target": green_iron_target,
        "copper_target": green_copper_target,
        "wood_target": green_wood_target,
        "rounds": int(
            getattr(namespace, "green_material_rounds", 0) or 0
        ),
        "planner_raw_requirements": green_raw,
    }
    journal.state["metrics"]["green_science_material_preflight"] = (
        green_preflight_diagnostics
    )
    journal.flush()
    if not green_preflight.accepted:
        journal.fail_stage(
            STAGE_GREEN_SCIENCE_INDUSTRY,
            "Green-science autonomous material preflight failed.",
        )
        journal.event(
            "counterexample",
            "Green-science material handoff from the autonomous backbone failed.",
            diagnostics=green_preflight_diagnostics,
        )
        return False

    code = f"""
# Green-industry commissioning is a separate electrical load.
move_to(coal_chest.position)
green_power_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
green_power_coal=0
if green_power_coal_available>0:
    green_power_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(24,green_power_coal_available),
    )
if green_power_coal>0:
    move_to(boiler.position)
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=green_power_coal,
    )

craft_item(Prototype.AssemblingMachine1,quantity=5)
craft_item(Prototype.SmallElectricPole,quantity=8)

cell_box=BuildingBox(width=30,height=12)
cell_area=nearest_buildable(
    Prototype.AssemblingMachine1,
    cell_box,
    science_assembler.position,
)
move_to(cell_area.center)

cable_machine=place_entity(
    Prototype.AssemblingMachine1,
    position=cell_area.center,
)
cable_machine=set_entity_recipe(cable_machine,Prototype.CopperCable)
connect_entities(steam_engine,cable_machine,Prototype.SmallElectricPole)

circuit_machine=place_entity_next_to(
    Prototype.AssemblingMachine1,
    cable_machine.position,
    direction=Direction.RIGHT,
    spacing=4,
)
circuit_machine=set_entity_recipe(
    circuit_machine,
    Prototype.ElectronicCircuit,
)
connect_entities(steam_engine,circuit_machine,Prototype.SmallElectricPole)

belt_machine=place_entity_next_to(
    Prototype.AssemblingMachine1,
    circuit_machine.position,
    direction=Direction.RIGHT,
    spacing=4,
)
belt_machine=set_entity_recipe(belt_machine,Prototype.TransportBelt)
connect_entities(steam_engine,belt_machine,Prototype.SmallElectricPole)

inserter_machine=place_entity_next_to(
    Prototype.AssemblingMachine1,
    belt_machine.position,
    direction=Direction.RIGHT,
    spacing=4,
)
inserter_machine=set_entity_recipe(inserter_machine,Prototype.Inserter)
connect_entities(steam_engine,inserter_machine,Prototype.SmallElectricPole)

green_machine=place_entity_next_to(
    Prototype.AssemblingMachine1,
    inserter_machine.position,
    direction=Direction.RIGHT,
    spacing=4,
)
green_machine=set_entity_recipe(
    green_machine,
    Prototype.LogisticsSciencePack,
)
connect_entities(steam_engine,green_machine,Prototype.SmallElectricPole)

# Cable -> circuit.
green_copper=min(60,inspect_inventory()[Prototype.CopperPlate])
if green_copper>0:
    cable_machine=insert_item(
        Prototype.CopperPlate,
        cable_machine,
        quantity=green_copper,
    )
sleep(45)
green_cable=inspect_inventory(cable_machine)[Prototype.CopperCable]
if green_cable>0:
    green_cable=extract_item(
        Prototype.CopperCable,
        cable_machine,
        quantity=green_cable,
    )

green_iron=min(90,inspect_inventory()[Prototype.IronPlate])
if green_cable>0:
    circuit_machine=insert_item(
        Prototype.CopperCable,
        circuit_machine,
        quantity=green_cable,
    )
if green_iron>0:
    circuit_machine=insert_item(
        Prototype.IronPlate,
        circuit_machine,
        quantity=min(30,green_iron),
    )
sleep(45)
green_circuits=inspect_inventory(
    circuit_machine,
)[Prototype.ElectronicCircuit]
if green_circuits>0:
    green_circuits=extract_item(
        Prototype.ElectronicCircuit,
        circuit_machine,
        quantity=green_circuits,
    )

# Produce shared gear pool for belt + inserter intermediates.
gear_source=min(50,inspect_inventory()[Prototype.IronPlate]//2)
if gear_source>0:
    craft_item(Prototype.IronGearWheel,quantity=gear_source)
green_gears=inspect_inventory()[Prototype.IronGearWheel]
green_iron=inspect_inventory()[Prototype.IronPlate]

if green_gears>0:
    belt_machine=insert_item(
        Prototype.IronGearWheel,
        belt_machine,
        quantity=min(20,green_gears),
    )
if green_iron>0:
    belt_machine=insert_item(
        Prototype.IronPlate,
        belt_machine,
        quantity=min(20,green_iron),
    )
if green_gears>0:
    inserter_machine=insert_item(
        Prototype.IronGearWheel,
        inserter_machine,
        quantity=min(20,green_gears),
    )
if green_iron>0:
    inserter_machine=insert_item(
        Prototype.IronPlate,
        inserter_machine,
        quantity=min(20,green_iron),
    )
if green_circuits>0:
    inserter_machine=insert_item(
        Prototype.ElectronicCircuit,
        inserter_machine,
        quantity=min(20,green_circuits),
    )
sleep(40)

green_belts=inspect_inventory(belt_machine)[Prototype.TransportBelt]
if green_belts>0:
    green_belts=extract_item(
        Prototype.TransportBelt,
        belt_machine,
        quantity=green_belts,
    )
green_inserters=inspect_inventory(inserter_machine)[Prototype.Inserter]
if green_inserters>0:
    green_inserters=extract_item(
        Prototype.Inserter,
        inserter_machine,
        quantity=green_inserters,
    )

if green_belts>0:
    green_machine=insert_item(
        Prototype.TransportBelt,
        green_machine,
        quantity=green_belts,
    )
if green_inserters>0:
    green_machine=insert_item(
        Prototype.Inserter,
        green_machine,
        quantity=green_inserters,
    )
sleep({settle_seconds})
open_green=inspect_inventory(
    green_machine,
)[Prototype.LogisticsSciencePack]
print({{
    'green_cable':green_cable,
    'green_circuits':green_circuits,
    'green_belts':green_belts,
    'green_inserters':green_inserters,
    'open_green':open_green,
}})
"""

    def accept(result: Any) -> bool:
        green = float(getattr(namespace, "open_green", 0) or 0)
        circuits = float(getattr(namespace, "green_circuits", 0) or 0)
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and circuits > 0
            and green > 0
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    green = float(getattr(namespace, "open_green", 0) or 0)
    circuits = float(getattr(namespace, "green_circuits", 0) or 0)
    journal.state["metrics"].update(
        {
            "open_play_electronic_circuits": circuits,
            "open_play_green_science": green,
            "green_science_material_preflight": green_preflight_diagnostics,
        }
    )
    if not step.accepted:
        journal.fail_stage(
            STAGE_GREEN_SCIENCE_INDUSTRY,
            "Powered open-play industrial DAG produced no validated green science.",
        )
        return False
    journal.complete_stage(
        STAGE_GREEN_SCIENCE_INDUSTRY,
        f"Open-play industry produced {green:.0f} green science packs.",
    )
    return True


def _research_logistics(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    instance = env.unwrapped.instance
    namespace = instance.namespace
    required_red = 20

    journal.set_stage(
        STAGE_LOGISTICS_RESEARCH,
        status="validating",
        detail=(
            "Researching Factorio 2.0 Logistics from its real requirement: "
            "20 automation science packs. Green science remains an industrial "
            "capability, not a fabricated prerequisite."
        ),
        next_action="validate Logistics with 20 powered red science packs",
    )

    prepare_code = f"""
# Recover any red science left in the powered assembler.
logistics_red_inventory=inspect_inventory()[Prototype.AutomationSciencePack]
logistics_red_machine=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
if logistics_red_machine>0:
    move_to(science_assembler.position)
    logistics_red_extracted=extract_item(
        Prototype.AutomationSciencePack,
        science_assembler,
        quantity=logistics_red_machine,
    )
else:
    logistics_red_extracted=0

logistics_red_available=inspect_inventory()[Prototype.AutomationSciencePack]
if logistics_red_available<{required_red}:
    logistics_missing={required_red}-logistics_red_available
    logistics_plate_rounds=0
    logistics_iron_target=logistics_missing*2
    logistics_copper_target=logistics_missing

    for logistics_plate_round in range(1,6):
        move_to(iron_output_chest.position)
        amount=inspect_inventory(
            iron_output_chest,
        )[Prototype.IronPlate]
        if amount>0:
            extract_item(
                Prototype.IronPlate,
                iron_output_chest,
                quantity=amount,
            )
        move_to(copper_output_chest.position)
        amount=inspect_inventory(
            copper_output_chest,
        )[Prototype.CopperPlate]
        if amount>0:
            extract_item(
                Prototype.CopperPlate,
                copper_output_chest,
                quantity=amount,
            )

        logistics_iron=inspect_inventory()[Prototype.IronPlate]
        logistics_copper=inspect_inventory()[Prototype.CopperPlate]
        logistics_plate_rounds=logistics_plate_round
        if (
            logistics_iron>=logistics_iron_target
            and logistics_copper>=logistics_copper_target
        ):
            break
        sleep(120)

    logistics_iron=inspect_inventory()[Prototype.IronPlate]
    logistics_copper=inspect_inventory()[Prototype.CopperPlate]
    logistics_gear_target=min(
        logistics_missing,
        logistics_iron//2,
    )
    if logistics_gear_target>0:
        craft_item(
            Prototype.IronGearWheel,
            quantity=logistics_gear_target,
        )
    logistics_gears=inspect_inventory()[Prototype.IronGearWheel]
    if logistics_gears>0:
        science_assembler=insert_item(
            Prototype.IronGearWheel,
            science_assembler,
            quantity=min(logistics_missing,logistics_gears),
        )
    if logistics_copper>0:
        science_assembler=insert_item(
            Prototype.CopperPlate,
            science_assembler,
            quantity=min(logistics_missing,logistics_copper),
        )

# Recommission power for production + research.
move_to(coal_chest.position)
logistics_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
logistics_coal=0
if logistics_coal_available>0:
    logistics_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(24,logistics_coal_available),
    )
if logistics_coal>0:
    move_to(boiler.position)
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=logistics_coal,
    )

logistics_red_available=inspect_inventory()[Prototype.AutomationSciencePack]
print({{
    'red_available':logistics_red_available,
    'coal_added':logistics_coal,
}})
"""
    preparation = executor.execute(
        prepare_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
    )
    if not preparation.accepted:
        diagnostics = {
            "phase": "preparation",
            "error_occurred": bool(
                preparation.info.get("error_occurred")
            ),
            "result": str(preparation.info.get("result", ""))[:1800],
        }
        journal.state["metrics"]["logistics_research_diagnostics"] = diagnostics
        journal.fail_stage(STAGE_LOGISTICS_RESEARCH, "Logistics research preparation failed.")
        journal.event(
            "counterexample",
            "Logistics preparation rejected.",
            diagnostics=diagnostics,
        )
        return False

    # Produce missing packs using bounded checkpoints.
    production_rounds: list[dict[str, Any]] = []
    red_available = float(
        getattr(namespace, "logistics_red_available", 0) or 0
    )
    for round_index in range(1, 3):
        if red_available >= required_red:
            break
        round_step = executor.execute(
            """sleep(120)
logistics_red_output=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
print({'output':logistics_red_output})""",
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        output = float(
            getattr(namespace, "logistics_red_output", 0) or 0
        )
        row = {
            "round": round_index,
            "accepted": round_step.accepted,
            "output": output,
            "error_occurred": bool(
                round_step.info.get("error_occurred")
            ),
            "result": str(round_step.info.get("result", ""))[:1000],
        }
        production_rounds.append(row)
        if not round_step.accepted:
            break
        if output > 0:
            extract_step = executor.execute(
                f"""move_to(science_assembler.position)
logistics_red_extracted=extract_item(
    Prototype.AutomationSciencePack,
    science_assembler,
    quantity=min({required_red},logistics_red_output),
)
logistics_red_available=inspect_inventory()[Prototype.AutomationSciencePack]
print({{'available':logistics_red_available}})""",
                accept=lambda result: (
                    not bool(result.info.get("error_occurred"))
                    and result.candidate_game_state is not None
                ),
                use_checkpoint_for_action=False,
            )
            if not extract_step.accepted:
                break
            red_available = float(
                getattr(namespace, "logistics_red_available", 0) or 0
            )
        journal.state["metrics"][
            "logistics_red_production_rounds"
        ] = production_rounds
        journal.flush()

    if red_available < required_red:
        diagnostics = {
            "phase": "red_production",
            "required": required_red,
            "available": red_available,
            "rounds": production_rounds,
        }
        journal.state["metrics"]["logistics_research_diagnostics"] = diagnostics
        journal.fail_stage(
            STAGE_LOGISTICS_RESEARCH,
            f"Logistics requires {required_red} red packs; only {red_available:.0f} available.",
        )
        journal.event(
            "counterexample",
            "Logistics red-science budget remained insufficient.",
            diagnostics=diagnostics,
        )
        return False

    feed_code = f"""
move_to(lab.position)
logistics_red_fed_count=min(
    {required_red},
    inspect_inventory()[Prototype.AutomationSciencePack],
)
lab=insert_item(
    Prototype.AutomationSciencePack,
    lab,
    quantity=logistics_red_fed_count,
)
set_research(Technology.Logistics)
logistics_remaining=str(get_research_progress(Technology.Logistics))
print({{'fed':logistics_red_fed_count,'remaining':logistics_remaining}})
"""
    feed_step = executor.execute(
        feed_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(
                getattr(namespace, "logistics_red_fed_count", 0) or 0
            )
            >= required_red
        ),
        use_checkpoint_for_action=False,
    )
    if not feed_step.accepted:
        journal.fail_stage(STAGE_LOGISTICS_RESEARCH, "Logistics lab feed failed.")
        return False

    research_rounds: list[dict[str, Any]] = []
    researched = _technology_researched(instance, "logistics")
    for round_index in range(1, 4):
        if researched:
            break
        round_step = executor.execute(
            """sleep(120)
logistics_remaining=str(get_research_progress(Technology.Logistics))
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
logistics_boiler_status=str(boiler.status)
logistics_engine_status=str(steam_engine.status)
print({
  'remaining':logistics_remaining,
  'boiler_status':logistics_boiler_status,
  'engine_status':logistics_engine_status,
})""",
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        researched = _technology_researched(instance, "logistics")
        research_rounds.append(
            {
                "round": round_index,
                "accepted": round_step.accepted,
                "researched": researched,
                "remaining": str(
                    getattr(namespace, "logistics_remaining", "")
                ),
                "boiler_status": str(
                    getattr(namespace, "logistics_boiler_status", "")
                ),
                "engine_status": str(
                    getattr(namespace, "logistics_engine_status", "")
                ),
                "error_occurred": bool(
                    round_step.info.get("error_occurred")
                ),
                "result": str(
                    round_step.info.get("result", "")
                )[:1200],
            }
        )
        journal.state["metrics"][
            "logistics_research_rounds"
        ] = research_rounds
        journal.flush()
        if not round_step.accepted:
            break

    journal.state["metrics"]["logistics_researched"] = researched
    journal.state["metrics"]["logistics_research_diagnostics"] = {
        "required_red": required_red,
        "red_available": red_available,
        "production_rounds": production_rounds,
        "research_rounds": research_rounds,
        "researched": researched,
    }
    if not researched:
        journal.fail_stage(
            STAGE_LOGISTICS_RESEARCH,
            "Logistics technology was not validated in bounded open-play research.",
        )
        journal.event(
            "counterexample",
            "Logistics research remained incomplete.",
            diagnostics=journal.state["metrics"]["logistics_research_diagnostics"],
        )
        return False

    journal.complete_stage(
        STAGE_LOGISTICS_RESEARCH,
        "Logistics researched from its real 20-red-science Factorio 2.0 requirement.",
    )
    journal.event(
        "technology",
        "Open-play researched Logistics after separately validating green-science industry.",
    )
    return True


def _electric_mining_transition(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    research_seconds: int,
) -> bool:
    instance = env.unwrapped.instance
    namespace = instance.namespace
    configuration = (
        journal.state.get("evolution", {})
        .get("validation_configuration", {})
    )
    if not isinstance(configuration, dict):
        configuration = {}
    layout_variant = int(
        configuration.get("autonomy_layout_variant", 0) or 0
    ) % 4
    commissioning_coal_target = max(
        8,
        min(
            32,
            int(
                configuration.get(
                    "autonomy_commissioning_coal",
                    14,
                )
                or 14
            ),
        ),
    )
    belt_margin = max(
        2,
        min(
            24,
            int(configuration.get("autonomy_belt_margin", 6) or 6),
        ),
    )
    pole_margin = max(
        4,
        min(
            36,
            int(configuration.get("autonomy_pole_margin", 10) or 10),
        ),
    )
    route_detour_margin = max(
        2,
        min(
            24,
            int(
                configuration.get(
                    "autonomy_route_detour_margin",
                    8,
                )
                or 8
            ),
        ),
    )

    (
        boiler_inserter_direction,
        iron_ore_direction,
        iron_fuel_direction,
        copper_ore_direction,
        copper_fuel_direction,
    ) = _autonomy_layout_directions(layout_variant)

    journal.set_stage(
        STAGE_ELECTRIC_MINING_TRANSITION,
        status="validating",
        detail=(
            "Researching electric mining, provisioning a DAG-sized commissioning "
            "buffer, then building physical coal, iron and copper logistics."
        ),
        next_action=(
            "replace agent-mediated fuel/material movement with electric "
            "drills, belts and inserters"
        ),
    )

    # Electric Mining Drill requires 25 automation science packs.
    electric_required_red = 25

    prepare_research_code = f"""
electric_red_available=inspect_inventory()[Prototype.AutomationSciencePack]
electric_red_machine=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
if electric_red_machine>0:
    move_to(science_assembler.position)
    extract_item(
        Prototype.AutomationSciencePack,
        science_assembler,
        quantity=electric_red_machine,
    )
electric_red_available=inspect_inventory()[Prototype.AutomationSciencePack]

if electric_red_available<{electric_required_red}:
    electric_missing={electric_required_red}-electric_red_available

    # Recover plate WIP before producing the research-sized red batch.
    move_to(iron_furnace.position)
    for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
        amount=inspect_inventory(furnace)[Prototype.IronPlate]
        if amount>0:
            extract_item(Prototype.IronPlate,furnace,quantity=amount)
    move_to(copper_furnace.position)
    for furnace in (copper_furnace,copper_furnace_2):
        amount=inspect_inventory(furnace)[Prototype.CopperPlate]
        if amount>0:
            extract_item(Prototype.CopperPlate,furnace,quantity=amount)

    electric_iron=inspect_inventory()[Prototype.IronPlate]
    electric_copper=inspect_inventory()[Prototype.CopperPlate]
    electric_gears=inspect_inventory()[Prototype.IronGearWheel]
    electric_gear_shortfall=max(0,electric_missing-electric_gears)
    electric_gear_craft=min(
        electric_gear_shortfall,
        electric_iron//2,
    )
    if electric_gear_craft>0:
        craft_item(
            Prototype.IronGearWheel,
            quantity=electric_gear_craft,
        )
    electric_gears=inspect_inventory()[Prototype.IronGearWheel]
    if electric_gears>0:
        science_assembler=insert_item(
            Prototype.IronGearWheel,
            science_assembler,
            quantity=min(electric_missing,electric_gears),
        )
    if electric_copper>0:
        science_assembler=insert_item(
            Prototype.CopperPlate,
            science_assembler,
            quantity=min(electric_missing,electric_copper),
        )

# Reclaim commissioning fuel parked in the smelting furnaces before
# provisioning the electric-mining research window. Earlier preflights can
# leave dozens of coal items stranded in furnace fuel inventories even when
# the central coal chest is empty.
electric_reclaimed_coal=0
for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    move_to(furnace.position)
    parked=inspect_inventory(furnace)[Prototype.Coal]
    if parked>0:
        electric_reclaimed_coal+=extract_item(
            Prototype.Coal,
            furnace,
            quantity=parked,
        )
for furnace in (copper_furnace,copper_furnace_2):
    move_to(furnace.position)
    parked=inspect_inventory(furnace)[Prototype.Coal]
    if parked>0:
        electric_reclaimed_coal+=extract_item(
            Prototype.Coal,
            furnace,
            quantity=parked,
        )

# Recommission power for the bounded production/research windows.
move_to(coal_chest.position)
electric_research_chest_coal=inspect_inventory(
    coal_chest,
)[Prototype.Coal]
if electric_research_chest_coal>0:
    extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(24,electric_research_chest_coal),
    )

electric_research_coal_available=inspect_inventory()[Prototype.Coal]
electric_research_coal=min(24,electric_research_coal_available)
if electric_research_coal>0:
    move_to(boiler.position)
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=electric_research_coal,
    )

boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
lab=get_entity(Prototype.Lab,lab.position)
electric_research_boiler_status=str(boiler.status)
electric_research_engine_status=str(steam_engine.status)
electric_research_lab_energy=float(lab.energy or 0)
print({{
  'available':electric_red_available,
  'machine':electric_red_machine,
  'reclaimed_coal':electric_reclaimed_coal,
  'chest_coal':electric_research_chest_coal,
  'coal_available':electric_research_coal_available,
  'coal_loaded':electric_research_coal,
  'boiler_status':electric_research_boiler_status,
  'engine_status':electric_research_engine_status,
  'lab_energy':electric_research_lab_energy,
}})
"""
    prepare_research = executor.execute(
        prepare_research_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
    )
    research_preparation_diagnostics = {
        "accepted": prepare_research.accepted,
        "error_occurred": bool(
            prepare_research.info.get("error_occurred")
        ),
        "result": str(prepare_research.info.get("result", ""))[:1800],
        "policy_execution_time": prepare_research.info.get(
            "policy_execution_time"
        ),
        "ticks": prepare_research.info.get("ticks"),
        "reclaimed_coal": float(
            getattr(namespace, "electric_reclaimed_coal", 0) or 0
        ),
        "chest_coal": float(
            getattr(namespace, "electric_research_chest_coal", 0) or 0
        ),
        "coal_available": float(
            getattr(namespace, "electric_research_coal_available", 0) or 0
        ),
        "coal_loaded": float(
            getattr(namespace, "electric_research_coal", 0) or 0
        ),
        "boiler_status": str(
            getattr(namespace, "electric_research_boiler_status", "")
        ),
        "engine_status": str(
            getattr(namespace, "electric_research_engine_status", "")
        ),
        "lab_energy": float(
            getattr(namespace, "electric_research_lab_energy", 0) or 0
        ),
    }
    journal.state["metrics"][
        "electric_mining_research_preparation"
    ] = research_preparation_diagnostics
    journal.flush()

    if not prepare_research.accepted:
        journal.fail_stage(
            STAGE_ELECTRIC_MINING_TRANSITION,
            "Electric Mining Drill research preparation failed.",
        )
        journal.event(
            "counterexample",
            "Electric mining power/material preparation rejected.",
            diagnostics=research_preparation_diagnostics,
        )
        return False

    electric_red_available = float(
        getattr(namespace, "electric_red_available", 0) or 0
    )
    production_rounds: list[dict[str, Any]] = []
    for round_index in range(1, 4):
        if electric_red_available >= electric_required_red:
            break
        round_step = executor.execute(
            """sleep(120)
science_assembler=get_entity(
    Prototype.AssemblingMachine1,
    science_assembler.position,
)
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
electric_red_output=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
electric_red_assembler_status=str(science_assembler.status)
electric_red_assembler_energy=float(science_assembler.energy or 0)
electric_red_boiler_status=str(boiler.status)
electric_red_engine_status=str(steam_engine.status)
print({
  'output':electric_red_output,
  'assembler_status':electric_red_assembler_status,
  'assembler_energy':electric_red_assembler_energy,
  'boiler_status':electric_red_boiler_status,
  'engine_status':electric_red_engine_status,
})""",
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        output = float(
            getattr(namespace, "electric_red_output", 0) or 0
        )
        production_rounds.append(
            {
                "round": round_index,
                "accepted": round_step.accepted,
                "error_occurred": bool(
                    round_step.info.get("error_occurred")
                ),
                "result": str(round_step.info.get("result", ""))[:1200],
                "output": output,
                "assembler_status": str(
                    getattr(namespace, "electric_red_assembler_status", "")
                ),
                "assembler_energy": float(
                    getattr(namespace, "electric_red_assembler_energy", 0)
                    or 0
                ),
                "boiler_status": str(
                    getattr(namespace, "electric_red_boiler_status", "")
                ),
                "engine_status": str(
                    getattr(namespace, "electric_red_engine_status", "")
                ),
            }
        )
        if not round_step.accepted:
            break
        if output > 0:
            extract_step = executor.execute(
                f"""move_to(science_assembler.position)
extract_item(
    Prototype.AutomationSciencePack,
    science_assembler,
    quantity=min({electric_required_red},electric_red_output),
)
electric_red_available=inspect_inventory()[Prototype.AutomationSciencePack]
print({{'available':electric_red_available}})""",
                accept=lambda result: (
                    not bool(result.info.get("error_occurred"))
                    and result.candidate_game_state is not None
                ),
                use_checkpoint_for_action=False,
            )
            if not extract_step.accepted:
                break
            electric_red_available = float(
                getattr(namespace, "electric_red_available", 0) or 0
            )
        journal.state["metrics"][
            "electric_mining_red_production_rounds"
        ] = production_rounds
        journal.flush()

    if electric_red_available < electric_required_red:
        diagnostics = {
            "phase": "research_material",
            "required_red": electric_required_red,
            "available_red": electric_red_available,
            "production_rounds": production_rounds,
        }
        journal.state["metrics"]["electric_mining_transition"] = diagnostics
        journal.fail_stage(
            STAGE_ELECTRIC_MINING_TRANSITION,
            "Electric Mining Drill red-science budget remained insufficient.",
        )
        journal.event(
            "counterexample",
            "Electric mining research material gate rejected.",
            diagnostics=diagnostics,
        )
        return False

    feed_research = executor.execute(
        f"""move_to(lab.position)
electric_red_fed_count=min(
    {electric_required_red},
    inspect_inventory()[Prototype.AutomationSciencePack],
)
lab=insert_item(
    Prototype.AutomationSciencePack,
    lab,
    quantity=electric_red_fed_count,
)
set_research("electric-mining-drill")
electric_mining_remaining=str(get_research_progress("electric-mining-drill"))
print({{'fed':electric_red_fed_count,'remaining':electric_mining_remaining}})""",
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(
                getattr(namespace, "electric_red_fed_count", 0) or 0
            )
            >= electric_required_red
        ),
        use_checkpoint_for_action=False,
    )
    if not feed_research.accepted:
        journal.fail_stage(
            STAGE_ELECTRIC_MINING_TRANSITION,
            "Electric Mining Drill lab feed failed.",
        )
        return False

    electric_research_rounds: list[dict[str, Any]] = []
    electric_researched = _technology_researched(
        instance,
        "electric-mining-drill",
    )
    for round_index in range(1, 4):
        if electric_researched:
            break
        round_step = executor.execute(
            """sleep(120)
electric_mining_remaining=str(get_research_progress("electric-mining-drill"))
# Avoid FLE 0.4.3 Lab entity deserialization while the lab input inventory
# is populated. Technology state plus power-generation telemetry are enough
# to validate this bounded research window.
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
electric_round_boiler_status=str(boiler.status)
electric_round_engine_status=str(steam_engine.status)
print({
  'remaining':electric_mining_remaining,
  'boiler_status':electric_round_boiler_status,
  'engine_status':electric_round_engine_status,
})""",
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        electric_researched = _technology_researched(
            instance,
            "electric-mining-drill",
        )
        electric_research_rounds.append(
            {
                "round": round_index,
                "accepted": round_step.accepted,
                "error_occurred": bool(
                    round_step.info.get("error_occurred")
                ),
                "result": str(
                    round_step.info.get("result", "")
                )[:1200],
                "policy_execution_time": round_step.info.get(
                    "policy_execution_time"
                ),
                "ticks": round_step.info.get("ticks"),
                "researched": electric_researched,
                "remaining": str(
                    getattr(namespace, "electric_mining_remaining", "")
                ),
                "boiler_status": str(
                    getattr(namespace, "electric_round_boiler_status", "")
                ),
                "engine_status": str(
                    getattr(namespace, "electric_round_engine_status", "")
                ),
            }
        )
        journal.state["metrics"][
            "electric_mining_research_rounds"
        ] = electric_research_rounds
        journal.flush()
        if not round_step.accepted:
            break

    if not electric_researched:
        diagnostics = {
            "phase": "research",
            "required_red": electric_required_red,
            "available_red": electric_red_available,
            "production_rounds": production_rounds,
            "research_rounds": electric_research_rounds,
            "researched": False,
        }
        journal.state["metrics"]["electric_mining_transition"] = diagnostics
        journal.fail_stage(
            STAGE_ELECTRIC_MINING_TRANSITION,
            "Electric Mining Drill research failed during bounded commissioning.",
        )
        journal.event(
            "counterexample",
            "Electric mining research gate rejected.",
            diagnostics=diagnostics,
        )
        return False

    # Before building the autonomous backbone, convert any remaining raw ore
    # into a bounded commissioning buffer. This remains explicitly manual and
    # therefore does not count toward the later autonomy soak.
    iron_target = 180
    copper_target = 55
    buffer_code = f"""
transition_iron_target={iron_target}
transition_copper_target={copper_target}

# Recover existing plate output first.
move_to(iron_furnace.position)
for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    count=inspect_inventory(furnace)[Prototype.IronPlate]
    if count>0:
        extract_item(Prototype.IronPlate,furnace,quantity=count)
move_to(copper_furnace.position)
for furnace in (copper_furnace,copper_furnace_2):
    count=inspect_inventory(furnace)[Prototype.CopperPlate]
    if count>0:
        extract_item(Prototype.CopperPlate,furnace,quantity=count)

# Pull a bounded coal commissioning budget from the mined stockpile.
move_to(coal_chest.position)
transition_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
transition_coal=0
if transition_coal_available>0:
    transition_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(40,transition_coal_available),
    )

# Feed remaining raw ore into the existing furnaces. No credit is given for
# this flow; it only creates the construction buffer for the autonomous cells.
move_to(iron_furnace.position)
for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    remaining=inspect_inventory()[Prototype.IronOre]
    if remaining>0:
        furnace=insert_item(
            Prototype.IronOre,
            furnace,
            quantity=min(50,remaining),
        )
    coal_now=inspect_inventory()[Prototype.Coal]
    if coal_now>0:
        furnace=insert_item(
            Prototype.Coal,
            furnace,
            quantity=min(6,coal_now),
        )

move_to(copper_furnace.position)
for furnace in (copper_furnace,copper_furnace_2):
    remaining=inspect_inventory()[Prototype.CopperOre]
    if remaining>0:
        furnace=insert_item(
            Prototype.CopperOre,
            furnace,
            quantity=min(50,remaining),
        )
    coal_now=inspect_inventory()[Prototype.Coal]
    if coal_now>0:
        furnace=insert_item(
            Prototype.Coal,
            furnace,
            quantity=min(6,coal_now),
        )

for transition_round in range(1,9):
    inventory=inspect_inventory()
    transition_iron=float(inventory[Prototype.IronPlate])
    transition_copper=float(inventory[Prototype.CopperPlate])
    if (
        transition_iron>=transition_iron_target
        and transition_copper>=transition_copper_target
    ):
        break
    sleep(24)
    move_to(iron_furnace.position)
    for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
        count=inspect_inventory(furnace)[Prototype.IronPlate]
        if count>0:
            extract_item(Prototype.IronPlate,furnace,quantity=count)
    move_to(copper_furnace.position)
    for furnace in (copper_furnace,copper_furnace_2):
        count=inspect_inventory(furnace)[Prototype.CopperPlate]
        if count>0:
            extract_item(Prototype.CopperPlate,furnace,quantity=count)

transition_inventory=inspect_inventory()
transition_iron=float(transition_inventory[Prototype.IronPlate])
transition_copper=float(transition_inventory[Prototype.CopperPlate])
print({{
  'iron':transition_iron,
  'copper':transition_copper,
  'iron_target':transition_iron_target,
  'copper_target':transition_copper_target,
}})
"""
    buffer_step = executor.execute(
        buffer_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
    )
    iron_buffer = float(
        getattr(namespace, "transition_iron", 0) or 0
    )
    copper_buffer = float(
        getattr(namespace, "transition_copper", 0) or 0
    )
    buffer_diagnostics = {
        "phase": "material_buffer",
        "accepted": buffer_step.accepted,
        "error_occurred": bool(
            buffer_step.info.get("error_occurred")
        ),
        "result": str(buffer_step.info.get("result", ""))[:1800],
        "iron_buffer": iron_buffer,
        "copper_buffer": copper_buffer,
        "iron_target": iron_target,
        "copper_target": copper_target,
    }
    journal.state["metrics"]["electric_transition_buffer"] = (
        buffer_diagnostics
    )
    if (
        not buffer_step.accepted
        or iron_buffer < iron_target
        or copper_buffer < copper_target
    ):
        journal.state["metrics"]["electric_mining_transition"] = {
            **buffer_diagnostics,
            "researched": True,
        }
        journal.fail_stage(
            STAGE_ELECTRIC_MINING_TRANSITION,
            (
                "Electric transition construction buffer insufficient: "
                f"iron={iron_buffer:.0f}/{iron_target}, "
                f"copper={copper_buffer:.0f}/{copper_target}."
            ),
        )
        journal.event(
            "counterexample",
            "Electric transition stopped on a typed construction-buffer deficit.",
            diagnostics=buffer_diagnostics,
        )
        return False

    # ------------------------------------------------------------------
    # Physical backbone: split into bounded transactions.
    #
    # FLE 0.4.3 evaluates each action with a hard 120 s wall-clock timeout.
    # A single action containing layout, route-buffer smelting, pathfinding
    # and a production soak is therefore not a valid experimental unit: an
    # otherwise-correct factory can be rejected merely because the action is
    # too coarse.  Each phase below has an independent acceptance gate and
    # persisted diagnostics.  No phase failure is promoted as success.
    # ------------------------------------------------------------------

    def fail_transition(
        *,
        phase: str,
        detail: str,
        event_message: str,
        diagnostics: dict[str, Any],
    ) -> bool:
        payload = {
            "phase": phase,
            "researched": True,
            "material_buffer": buffer_diagnostics,
            **diagnostics,
        }
        journal.state["metrics"]["electric_mining_transition"] = payload
        journal.fail_stage(
            STAGE_ELECTRIC_MINING_TRANSITION,
            detail,
        )
        journal.event(
            "counterexample",
            event_message,
            diagnostics=payload,
        )
        return False

    # Phase A — place the future backbone and derive deterministic material
    # bounds.  FLE's get_connection_amount dry-run is intentionally excluded
    # from the critical path: in FLE 0.4.3 a failed belt dry-run can surface an
    # Inserter object instead of the expected requirement mapping.  Geometry is
    # used only for conservative material preflight; live connect_entities
    # calls below remain the authoritative topology experiment.
    layout_code = """
# Seed the boiler once. Commissioning fuel is explicitly pre-autonomy.
move_to(coal_chest.position)
commissioning_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
commissioning_coal=0
if commissioning_coal_available>0:
    commissioning_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(__COMMISSIONING_COAL__,commissioning_coal_available),
    )
if commissioning_coal>0:
    move_to(boiler.position)
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=commissioning_coal,
    )

craft_item(Prototype.ElectricMiningDrill,quantity=5)
craft_item(Prototype.Inserter,quantity=7)
craft_item(Prototype.WoodenChest,quantity=2)

# The burner drill belongs to commissioning and must not contaminate the
# electric closed-loop evidence.
pickup_entity(coal_drill)

# Coal -> boiler.
move_to(coal_pos)
electric_coal_drill=place_entity(
    Prototype.ElectricMiningDrill,
    position=coal_pos,
    direction=Direction.DOWN,
    exact=False,
)
move_to(boiler.position)
boiler_inserter=place_entity_next_to(
    Prototype.Inserter,
    boiler.position,
    direction=Direction.__BOILER_DIRECTION__,
    spacing=0,
)
boiler_inserter=rotate_entity(
    boiler_inserter,
    Direction.__BOILER_ROTATE_DIRECTION__,
)

# Iron ore + independent coal fuel feed.
move_to(iron_pos)
electric_iron_drill=place_entity(
    Prototype.ElectricMiningDrill,
    position=iron_pos,
    direction=Direction.DOWN,
    exact=False,
)
move_to(iron_furnace.position)
iron_ore_inserter=place_entity_next_to(
    Prototype.Inserter,
    iron_furnace.position,
    direction=Direction.__IRON_ORE_DIRECTION__,
    spacing=0,
)
iron_ore_inserter=rotate_entity(
    iron_ore_inserter,
    Direction.__IRON_ORE_ROTATE_DIRECTION__,
)
iron_fuel_inserter=place_entity_next_to(
    Prototype.Inserter,
    iron_furnace.position,
    direction=Direction.__IRON_FUEL_DIRECTION__,
    spacing=0,
)
iron_fuel_inserter=rotate_entity(
    iron_fuel_inserter,
    Direction.__IRON_FUEL_ROTATE_DIRECTION__,
)
iron_output_inserter=place_entity_next_to(
    Prototype.Inserter,
    iron_furnace.position,
    direction=Direction.DOWN,
    spacing=0,
)
iron_output_chest=place_entity_next_to(
    Prototype.WoodenChest,
    iron_output_inserter.position,
    direction=Direction.DOWN,
    spacing=0,
)
move_to(coal_pos)
iron_fuel_drill=place_entity(
    Prototype.ElectricMiningDrill,
    position=coal_pos,
    direction=Direction.DOWN,
    exact=False,
)

# Copper ore + independent coal fuel feed.
move_to(copper_pos)
electric_copper_drill=place_entity(
    Prototype.ElectricMiningDrill,
    position=copper_pos,
    direction=Direction.DOWN,
    exact=False,
)
move_to(copper_furnace.position)
copper_ore_inserter=place_entity_next_to(
    Prototype.Inserter,
    copper_furnace.position,
    direction=Direction.__COPPER_ORE_DIRECTION__,
    spacing=0,
)
copper_ore_inserter=rotate_entity(
    copper_ore_inserter,
    Direction.__COPPER_ORE_ROTATE_DIRECTION__,
)
copper_fuel_inserter=place_entity_next_to(
    Prototype.Inserter,
    copper_furnace.position,
    direction=Direction.__COPPER_FUEL_DIRECTION__,
    spacing=0,
)
copper_fuel_inserter=rotate_entity(
    copper_fuel_inserter,
    Direction.__COPPER_FUEL_ROTATE_DIRECTION__,
)
copper_output_inserter=place_entity_next_to(
    Prototype.Inserter,
    copper_furnace.position,
    direction=Direction.DOWN,
    spacing=0,
)
copper_output_chest=place_entity_next_to(
    Prototype.WoodenChest,
    copper_output_inserter.position,
    direction=Direction.DOWN,
    spacing=0,
)
move_to(coal_pos)
copper_fuel_drill=place_entity(
    Prototype.ElectricMiningDrill,
    position=coal_pos,
    direction=Direction.DOWN,
    exact=False,
)

def route_endpoint(entity,is_source):
    if is_source and hasattr(entity,'drop_position'):
        return entity.drop_position
    if (not is_source) and hasattr(entity,'pickup_position'):
        return entity.pickup_position
    return entity.position

def belt_upper_bound(label,source,target):
    source_pos=route_endpoint(source,True)
    target_pos=route_endpoint(target,False)
    manhattan=(
        abs(float(source_pos.x)-float(target_pos.x))
        +abs(float(source_pos.y)-float(target_pos.y))
    )
    count=max(
        1,
        int(manhattan+0.999999)+__ROUTE_DETOUR_MARGIN__,
    )
    return {
        'label':label,
        'source':str(source_pos),
        'target':str(target_pos),
        'manhattan':manhattan,
        'required':count,
        'method':'manhattan_plus_adaptive_detour_margin',
        'detour_margin':__ROUTE_DETOUR_MARGIN__,
    }

belt_requirement_estimates=[
    belt_upper_bound('coal_to_boiler',electric_coal_drill,boiler_inserter),
    belt_upper_bound('iron_ore',electric_iron_drill,iron_ore_inserter),
    belt_upper_bound('iron_fuel',iron_fuel_drill,iron_fuel_inserter),
    belt_upper_bound('copper_ore',electric_copper_drill,copper_ore_inserter),
    belt_upper_bound('copper_fuel',copper_fuel_drill,copper_fuel_inserter),
]
belt_required=sum(row['required'] for row in belt_requirement_estimates)

# Build a shared minimum-spanning power backbone instead of repeatedly
# paying a full steam-engine-to-consumer star connection. The real
# connect_entities calls later use exactly these edges.
power_nodes=[
    ('steam_engine',steam_engine),
    ('coal_drill',electric_coal_drill),
    ('boiler_inserter',boiler_inserter),
    ('iron_drill',electric_iron_drill),
    ('iron_ore_inserter',iron_ore_inserter),
    ('iron_fuel_inserter',iron_fuel_inserter),
    ('iron_output_inserter',iron_output_inserter),
    ('iron_fuel_drill',iron_fuel_drill),
    ('copper_drill',electric_copper_drill),
    ('copper_ore_inserter',copper_ore_inserter),
    ('copper_fuel_inserter',copper_fuel_inserter),
    ('copper_output_inserter',copper_output_inserter),
    ('copper_fuel_drill',copper_fuel_drill),
]
power_connected=[power_nodes[0]]
power_remaining=list(power_nodes[1:])
power_edges=[]
power_connection_group=None
pole_requirement_estimates=[]
pole_required=0
while power_remaining:
    best=None
    for source_label,source_entity in power_connected:
        for target_label,target_entity in power_remaining:
            dx=float(source_entity.position.x)-float(target_entity.position.x)
            dy=float(source_entity.position.y)-float(target_entity.position.y)
            distance=(dx*dx+dy*dy)**0.5
            if best is None or distance<best[0]:
                best=(
                    distance,
                    source_label,
                    source_entity,
                    target_label,
                    target_entity,
                )
    (
        distance,
        source_label,
        source_entity,
        target_label,
        target_entity,
    )=best
    required=max(1,int(distance/6.0+0.999999)+2)
    pole_required+=required
    power_edges.append((
        source_label,
        source_entity,
        target_label,
        target_entity,
    ))
    pole_requirement_estimates.append({
        'label':target_label,
        'source':source_label,
        'target':target_label,
        'distance':distance,
        'required':required,
        'method':'mst_euclidean_span_6_plus_2',
    })
    power_connected.append((target_label,target_entity))
    power_remaining=[
        row for row in power_remaining
        if row[0]!=target_label
    ]

belt_build_count=int(belt_required)+__BELT_MARGIN__
pole_build_count=int(pole_required)+__POLE_MARGIN__

# Recipes: 2 belts <- 3 iron; 2 small poles <- 1 copper plate + 1 wood.
route_iron_target=int((belt_build_count*3+1)//2)+12
route_copper_target=int((pole_build_count+1)//2)+8
route_wood_target=int((pole_build_count+1)//2)+8

route_initial_inventory=inspect_inventory()
route_initial_iron=float(route_initial_inventory[Prototype.IronPlate])
route_initial_copper=float(route_initial_inventory[Prototype.CopperPlate])
route_initial_wood=float(route_initial_inventory[Prototype.Wood])

iron_deficit=max(0.0,route_iron_target-route_initial_iron)
copper_deficit=max(0.0,route_copper_target-route_initial_copper)

# Bound the experiment from conservative observed throughput, not ideal recipe
# throughput. A prior counterexample reached only ~13 iron plates per 24 s
# round with three furnaces because movement, extraction and refuelling consume
# part of each batch. The lower bounds below deliberately under-estimate that
# throughput and add an explicit tail margin. Phase B additionally aborts on a
# measured production plateau, so this larger horizon cannot spin forever.
iron_rounds_needed=int(iron_deficit/10.0+0.999999)
copper_rounds_needed=int(copper_deficit/8.0+0.999999)
route_buffer_max_rounds=min(
    64,
    max(
        16,
        iron_rounds_needed+8,
        copper_rounds_needed+8,
    ),
)
print({
  'commissioning_coal':commissioning_coal,
  'belt_required':belt_required,
  'pole_required':pole_required,
  'belt_build_count':belt_build_count,
  'pole_build_count':pole_build_count,
  'route_iron_target':route_iron_target,
  'route_copper_target':route_copper_target,
  'route_wood_target':route_wood_target,
  'route_initial_iron':route_initial_iron,
  'route_initial_copper':route_initial_copper,
  'route_initial_wood':route_initial_wood,
  'route_buffer_max_rounds':route_buffer_max_rounds,
})
"""
    layout_code = (
        layout_code
        .replace("__COMMISSIONING_COAL__", str(commissioning_coal_target))
        .replace(
            "__BOILER_ROTATE_DIRECTION__",
            _opposite_direction_name(boiler_inserter_direction),
        )
        .replace(
            "__IRON_ORE_ROTATE_DIRECTION__",
            _opposite_direction_name(iron_ore_direction),
        )
        .replace(
            "__IRON_FUEL_ROTATE_DIRECTION__",
            _opposite_direction_name(iron_fuel_direction),
        )
        .replace(
            "__COPPER_ORE_ROTATE_DIRECTION__",
            _opposite_direction_name(copper_ore_direction),
        )
        .replace(
            "__COPPER_FUEL_ROTATE_DIRECTION__",
            _opposite_direction_name(copper_fuel_direction),
        )
        .replace("__BOILER_DIRECTION__", boiler_inserter_direction)
        .replace("__IRON_ORE_DIRECTION__", iron_ore_direction)
        .replace("__IRON_FUEL_DIRECTION__", iron_fuel_direction)
        .replace("__COPPER_ORE_DIRECTION__", copper_ore_direction)
        .replace("__COPPER_FUEL_DIRECTION__", copper_fuel_direction)
        .replace("__BELT_MARGIN__", str(belt_margin))
        .replace("__POLE_MARGIN__", str(pole_margin))
        .replace(
            "__ROUTE_DETOUR_MARGIN__",
            str(route_detour_margin),
        )
    )

    layout_step = executor.execute(
        layout_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
    )
    layout_diagnostics = {
        "accepted": layout_step.accepted,
        "error_occurred": bool(layout_step.info.get("error_occurred")),
        "result": str(layout_step.info.get("result", ""))[:2200],
        "policy_execution_time": layout_step.info.get("policy_execution_time"),
        "ticks": layout_step.info.get("ticks"),
        "requirement_method": "deterministic_geometry_upper_bound",
        "belt_required": float(getattr(namespace, "belt_required", 0) or 0),
        "pole_required": float(getattr(namespace, "pole_required", 0) or 0),
        "belt_build_count": int(
            getattr(namespace, "belt_build_count", 0) or 0
        ),
        "pole_build_count": int(
            getattr(namespace, "pole_build_count", 0) or 0
        ),
        "belt_requirement_estimates": list(
            getattr(namespace, "belt_requirement_estimates", []) or []
        ),
        "pole_requirement_estimates": list(
            getattr(namespace, "pole_requirement_estimates", []) or []
        ),
        "route_iron": float(
            getattr(namespace, "route_initial_iron", 0) or 0
        ),
        "route_iron_target": float(
            getattr(namespace, "route_iron_target", 0) or 0
        ),
        "route_copper": float(
            getattr(namespace, "route_initial_copper", 0) or 0
        ),
        "route_copper_target": float(
            getattr(namespace, "route_copper_target", 0) or 0
        ),
        "route_wood": float(
            getattr(namespace, "route_initial_wood", 0) or 0
        ),
        "route_wood_target": float(
            getattr(namespace, "route_wood_target", 0) or 0
        ),
        "route_buffer_max_rounds": int(
            getattr(namespace, "route_buffer_max_rounds", 0) or 0
        ),
        "commissioning_coal": float(
            getattr(namespace, "commissioning_coal", 0) or 0
        ),
        "layout_variant": layout_variant,
        "route_detour_margin": route_detour_margin,
        "layout": {
            "boiler_inserter": boiler_inserter_direction,
            "iron_ore_inserter": iron_ore_direction,
            "iron_fuel_inserter": iron_fuel_direction,
            "copper_ore_inserter": copper_ore_direction,
            "copper_fuel_inserter": copper_fuel_direction,
        },
    }
    journal.state["metrics"]["electric_mining_layout_plan"] = layout_diagnostics
    journal.flush()

    if not layout_step.accepted:
        return fail_transition(
            phase="layout_plan",
            detail="Electric transition layout/material planning failed.",
            event_message="Electric transition layout plan rejected.",
            diagnostics=layout_diagnostics,
        )

    route_iron_target = float(layout_diagnostics["route_iron_target"])
    route_copper_target = float(layout_diagnostics["route_copper_target"])
    route_wood_target = float(layout_diagnostics["route_wood_target"])
    route_iron = float(layout_diagnostics["route_iron"])
    route_copper = float(layout_diagnostics["route_copper"])
    route_wood = float(layout_diagnostics["route_wood"])
    route_buffer_max_rounds = int(
        layout_diagnostics["route_buffer_max_rounds"]
    )

    # Wood cannot be synthesized by this stage, so reject before spending
    # bounded smelting time on an impossible run.
    if route_wood < route_wood_target:
        return fail_transition(
            phase="route_buffer",
            detail=(
                "Electric transition wood buffer insufficient: "
                f"{route_wood:.0f}/{route_wood_target:.0f}."
            ),
            event_message="Electric transition stopped on a typed wood deficit.",
            diagnostics={
                **layout_diagnostics,
                "route_buffer_rounds": 0,
                "route_buffer_batches": [],
            },
        )

    # Phase B — smelt route material in independently committed <=4-round
    # batches.  This makes the 120 s FLE action timeout irrelevant to the
    # total bounded horizon while preserving a hard experiment-level cap.
    route_batches: list[dict[str, Any]] = []
    route_buffer_rounds = 0
    route_batch_size = 4
    route_no_progress_batches = 0

    while (
        route_buffer_rounds < route_buffer_max_rounds
        and (
            route_iron < route_iron_target
            or route_copper < route_copper_target
        )
    ):
        rounds_requested = min(
            route_batch_size,
            route_buffer_max_rounds - route_buffer_rounds,
        )
        batch_code = f"""
route_batch_rounds=0
route_batch_reclaimed_coal=0
for route_batch_round in range(1,{rounds_requested + 1}):
    route_inventory=inspect_inventory()
    route_iron=float(route_inventory[Prototype.IronPlate])
    route_copper=float(route_inventory[Prototype.CopperPlate])
    route_wood=float(route_inventory[Prototype.Wood])
    if (
        route_iron>={route_iron_target}
        and route_copper>={route_copper_target}
    ):
        break

    # Reclaim fuel stranded in idle commissioning furnaces before asking
    # the central coal stockpile for more. A previous route-buffer failure
    # demonstrated 48 coal parked in empty furnaces while 100 iron ore sat
    # unfuelled in the two productive iron furnaces. Fuel is therefore
    # rebalanced by work state, not by static furnace identity.
    for parked_furnace in (
        iron_furnace,
        iron_furnace_2,
        iron_furnace_3,
        copper_furnace,
        copper_furnace_2,
    ):
        parked_inventory=inspect_inventory(parked_furnace)
        parked_iron=parked_inventory[Prototype.IronOre]
        parked_copper=parked_inventory[Prototype.CopperOre]
        parked_coal=parked_inventory[Prototype.Coal]
        if (
            parked_coal>0
            and parked_iron<=0
            and parked_copper<=0
        ):
            reclaimed=extract_item(
                Prototype.Coal,
                parked_furnace,
                quantity=parked_coal,
            )
            route_batch_reclaimed_coal+=reclaimed

    player_coal=inspect_inventory()[Prototype.Coal]
    if player_coal<24:
        move_to(coal_chest.position)
        refill_coal=inspect_inventory(coal_chest)[Prototype.Coal]
        if refill_coal>0:
            extract_item(
                Prototype.Coal,
                coal_chest,
                quantity=min(24-player_coal,refill_coal),
            )

    move_to(iron_furnace.position)
    for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
        output=inspect_inventory(furnace)[Prototype.IronPlate]
        if output>0:
            extract_item(Prototype.IronPlate,furnace,quantity=output)

        remaining=inspect_inventory()[Prototype.IronOre]
        iron_input=inspect_inventory(furnace)[Prototype.IronOre]
        iron_room=max(0,50-int(iron_input))
        if remaining>0 and iron_room>0:
            furnace=insert_item(
                Prototype.IronOre,
                furnace,
                quantity=min(iron_room,remaining),
            )

        coal_now=inspect_inventory()[Prototype.Coal]
        furnace_coal=inspect_inventory(furnace)[Prototype.Coal]
        coal_room=max(0,10-int(furnace_coal))
        if coal_now>0 and coal_room>0:
            furnace=insert_item(
                Prototype.Coal,
                furnace,
                quantity=min(4,coal_room,coal_now),
            )

    move_to(copper_furnace.position)
    for furnace in (copper_furnace,copper_furnace_2):
        output=inspect_inventory(furnace)[Prototype.CopperPlate]
        if output>0:
            extract_item(Prototype.CopperPlate,furnace,quantity=output)

        remaining=inspect_inventory()[Prototype.CopperOre]
        copper_input=inspect_inventory(furnace)[Prototype.CopperOre]
        copper_room=max(0,50-int(copper_input))
        if remaining>0 and copper_room>0:
            furnace=insert_item(
                Prototype.CopperOre,
                furnace,
                quantity=min(copper_room,remaining),
            )

        coal_now=inspect_inventory()[Prototype.Coal]
        furnace_coal=inspect_inventory(furnace)[Prototype.Coal]
        coal_room=max(0,10-int(furnace_coal))
        if coal_now>0 and coal_room>0:
            furnace=insert_item(
                Prototype.Coal,
                furnace,
                quantity=min(4,coal_room,coal_now),
            )

    sleep(24)
    route_batch_rounds+=1

# Recover the output produced by the final sleep in this batch.
move_to(iron_furnace.position)
for furnace in (iron_furnace,iron_furnace_2,iron_furnace_3):
    output=inspect_inventory(furnace)[Prototype.IronPlate]
    if output>0:
        extract_item(Prototype.IronPlate,furnace,quantity=output)
move_to(copper_furnace.position)
for furnace in (copper_furnace,copper_furnace_2):
    output=inspect_inventory(furnace)[Prototype.CopperPlate]
    if output>0:
        extract_item(Prototype.CopperPlate,furnace,quantity=output)

route_inventory=inspect_inventory()
route_iron=float(route_inventory[Prototype.IronPlate])
route_copper=float(route_inventory[Prototype.CopperPlate])
route_wood=float(route_inventory[Prototype.Wood])
print({{
  'rounds':route_batch_rounds,
  'iron':route_iron,
  'copper':route_copper,
  'wood':route_wood,
  'reclaimed_coal':route_batch_reclaimed_coal,
}})
"""
        batch_step = executor.execute(
            batch_code,
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        completed = int(
            getattr(namespace, "route_batch_rounds", 0) or 0
        )
        previous_route_iron = route_iron
        previous_route_copper = route_copper
        route_buffer_rounds += completed
        route_iron = float(getattr(namespace, "route_iron", 0) or 0)
        route_copper = float(getattr(namespace, "route_copper", 0) or 0)
        route_wood = float(getattr(namespace, "route_wood", 0) or 0)
        iron_gain = max(0.0, route_iron - previous_route_iron)
        copper_gain = max(0.0, route_copper - previous_route_copper)
        still_needs_material = (
            route_iron < route_iron_target
            or route_copper < route_copper_target
        )
        if (
            batch_step.accepted
            and completed > 0
            and still_needs_material
            and iron_gain <= 0.0
            and copper_gain <= 0.0
        ):
            route_no_progress_batches += 1
        else:
            route_no_progress_batches = 0

        batch_diagnostics = {
            "batch": len(route_batches) + 1,
            "accepted": batch_step.accepted,
            "error_occurred": bool(
                batch_step.info.get("error_occurred")
            ),
            "result": str(batch_step.info.get("result", ""))[:1600],
            "policy_execution_time": batch_step.info.get(
                "policy_execution_time"
            ),
            "ticks": batch_step.info.get("ticks"),
            "rounds_requested": rounds_requested,
            "rounds_completed": completed,
            "rounds_total": route_buffer_rounds,
            "iron": route_iron,
            "copper": route_copper,
            "wood": route_wood,
            "iron_gain": iron_gain,
            "copper_gain": copper_gain,
            "no_progress_batches": route_no_progress_batches,
            "reclaimed_coal": float(
                getattr(namespace, "route_batch_reclaimed_coal", 0) or 0
            ),
        }
        route_batches.append(batch_diagnostics)
        journal.state["metrics"]["electric_mining_route_batches"] = (
            route_batches
        )
        journal.flush()

        if not batch_step.accepted:
            return fail_transition(
                phase="route_buffer",
                detail="Electric transition route-buffer batch failed.",
                event_message="Electric route-buffer transaction rejected.",
                diagnostics={
                    **layout_diagnostics,
                    "route_iron": route_iron,
                    "route_copper": route_copper,
                    "route_wood": route_wood,
                    "route_buffer_rounds": route_buffer_rounds,
                    "route_buffer_batches": route_batches,
                    "result": batch_diagnostics["result"],
                },
            )

        if completed <= 0 or route_no_progress_batches >= 2:
            break

    belt_build_count = int(
        layout_diagnostics.get("belt_build_count", 0) or 0
    )
    pole_build_count = int(
        layout_diagnostics.get("pole_build_count", 0) or 0
    )
    material_ledger = MaterialLedger.from_inventory(
        {
            "iron-plate": route_iron,
            "copper-plate": route_copper,
            "wood": route_wood,
        },
        safety_stock={
            "iron-plate": 12.0,
            "copper-plate": 8.0,
            "wood": 8.0,
        },
    )
    construction_plan = material_ledger.plan(
        {
            "iron-plate": float((belt_build_count * 3 + 1) // 2),
            "copper-plate": float((pole_build_count + 1) // 2),
            "wood": float((pole_build_count + 1) // 2),
        }
    )
    route_buffer_diagnostics = {
        **layout_diagnostics,
        "route_iron": route_iron,
        "route_copper": route_copper,
        "route_wood": route_wood,
        "route_buffer_rounds": route_buffer_rounds,
        "route_buffer_batches": route_batches,
        "route_no_progress_batches": route_no_progress_batches,
        "route_stop_reason": (
            "production_plateau"
            if route_no_progress_batches >= 2
            else (
                "round_cap"
                if route_buffer_rounds >= route_buffer_max_rounds
                else "target_reached"
            )
        ),
        "material_ledger": construction_plan.to_dict(),
    }
    journal.state["metrics"]["electric_mining_route_buffer"] = (
        route_buffer_diagnostics
    )
    journal.flush()

    route_deficits = []
    if route_iron < route_iron_target:
        route_deficits.append(
            f"iron={route_iron:.0f}/{route_iron_target:.0f}"
        )
    if route_copper < route_copper_target:
        route_deficits.append(
            f"copper={route_copper:.0f}/{route_copper_target:.0f}"
        )
    if route_wood < route_wood_target:
        route_deficits.append(
            f"wood={route_wood:.0f}/{route_wood_target:.0f}"
        )
    if route_deficits:
        return fail_transition(
            phase="route_buffer",
            detail=(
                "Electric transition route buffer insufficient: "
                + "; ".join(route_deficits)
            ),
            event_message="Electric transition stopped on a typed route-buffer deficit.",
            diagnostics={
                **route_buffer_diagnostics,
                "route_deficits": route_deficits,
            },
        )

    # Phase C — remove all manually loaded WIP from the two permanent furnaces
    # and retire the three commissioning-only furnaces.  Therefore the later
    # production deltas cannot be explained by residual manual ore/fuel.
    materials_code = f"""
for furnace,ore,plate in (
    (iron_furnace,Prototype.IronOre,Prototype.IronPlate),
    (iron_furnace_2,Prototype.IronOre,Prototype.IronPlate),
    (iron_furnace_3,Prototype.IronOre,Prototype.IronPlate),
    (copper_furnace,Prototype.CopperOre,Prototype.CopperPlate),
    (copper_furnace_2,Prototype.CopperOre,Prototype.CopperPlate),
):
    move_to(furnace.position)
    output=inspect_inventory(furnace)[plate]
    if output>0:
        extract_item(plate,furnace,quantity=output)
    raw=inspect_inventory(furnace)[ore]
    if raw>0:
        extract_item(ore,furnace,quantity=raw)
    fuel=inspect_inventory(furnace)[Prototype.Coal]
    if fuel>0:
        extract_item(Prototype.Coal,furnace,quantity=fuel)

pickup_entity(iron_furnace_2)
pickup_entity(iron_furnace_3)
pickup_entity(copper_furnace_2)

craft_item(
    Prototype.TransportBelt,
    quantity={int(layout_diagnostics["belt_build_count"])},
)
craft_item(
    Prototype.SmallElectricPole,
    quantity={int(layout_diagnostics["pole_build_count"])},
)
construction_belts=inspect_inventory()[Prototype.TransportBelt]
construction_poles=inspect_inventory()[Prototype.SmallElectricPole]
iron_primary_input=inspect_inventory(iron_furnace)[Prototype.IronOre]
iron_primary_fuel=inspect_inventory(iron_furnace)[Prototype.Coal]
copper_primary_input=inspect_inventory(copper_furnace)[Prototype.CopperOre]
copper_primary_fuel=inspect_inventory(copper_furnace)[Prototype.Coal]
print({{
  'belts':construction_belts,
  'poles':construction_poles,
  'iron_input':iron_primary_input,
  'iron_fuel':iron_primary_fuel,
  'copper_input':copper_primary_input,
  'copper_fuel':copper_primary_fuel,
}})
"""
    materials_step = executor.execute(
        materials_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "iron_primary_input", -1) or 0) == 0
            and float(getattr(namespace, "iron_primary_fuel", -1) or 0) == 0
            and float(getattr(namespace, "copper_primary_input", -1) or 0) == 0
            and float(getattr(namespace, "copper_primary_fuel", -1) or 0) == 0
        ),
        use_checkpoint_for_action=False,
    )
    materials_diagnostics = {
        "accepted": materials_step.accepted,
        "error_occurred": bool(
            materials_step.info.get("error_occurred")
        ),
        "result": str(materials_step.info.get("result", ""))[:1800],
        "policy_execution_time": materials_step.info.get(
            "policy_execution_time"
        ),
        "ticks": materials_step.info.get("ticks"),
        "belts": float(
            getattr(namespace, "construction_belts", 0) or 0
        ),
        "poles": float(
            getattr(namespace, "construction_poles", 0) or 0
        ),
        "primary_furnaces_cleared": {
            "iron_input": float(
                getattr(namespace, "iron_primary_input", 0) or 0
            ),
            "iron_fuel": float(
                getattr(namespace, "iron_primary_fuel", 0) or 0
            ),
            "copper_input": float(
                getattr(namespace, "copper_primary_input", 0) or 0
            ),
            "copper_fuel": float(
                getattr(namespace, "copper_primary_fuel", 0) or 0
            ),
        },
    }
    journal.state["metrics"]["electric_mining_construction_materials"] = (
        materials_diagnostics
    )
    journal.flush()

    if not materials_step.accepted:
        return fail_transition(
            phase="construction_materials",
            detail="Electric transition construction-material preparation failed.",
            event_message="Electric construction-material transaction rejected.",
            diagnostics={
                **route_buffer_diagnostics,
                "construction_materials": materials_diagnostics,
            },
        )

    # Phase D — connect the shared minimum-spanning power backbone in
    # bounded transactions. Each edge joins a new consumer to the already
    # connected component, so pole infrastructure can be reused physically.
    power_edge_count = len(
        layout_diagnostics.get("pole_requirement_estimates", [])
    )
    power_batch_size = 4
    power_batches: list[dict[str, Any]] = []
    for edge_start in range(0, power_edge_count, power_batch_size):
        edge_end = min(power_edge_count, edge_start + power_batch_size)
        power_code = f"""
power_batch_edges=[]
for (
    source_label,
    source_entity,
    target_label,
    target_entity,
) in power_edges[{edge_start}:{edge_end}]:
    poles_before=(
        len(getattr(power_connection_group,'poles',[]))
        if power_connection_group is not None
        else 0
    )
    if power_connection_group is None:
        power_connection_group=connect_entities(
            steam_engine,
            target_entity,
            Prototype.SmallElectricPole,
        )
        attachment='generator_to_consumer'
    else:
        power_connection_group=connect_entities(
            target_entity,
            power_connection_group,
            Prototype.SmallElectricPole,
        )
        attachment='consumer_to_existing_group'
    poles_after=len(getattr(power_connection_group,'poles',[]))
    power_batch_edges.append({{
        'planned_source':source_label,
        'target':target_label,
        'attachment':attachment,
        'poles_before':poles_before,
        'poles_after':poles_after,
        'poles_added':max(0,poles_after-poles_before),
    }})
print({{'edges':power_batch_edges}})
"""
        power_step = executor.execute(
            power_code,
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
            use_checkpoint_for_action=False,
        )
        batch_edges = list(
            getattr(namespace, "power_batch_edges", []) or []
        )
        batch_diag = {
            "batch": len(power_batches) + 1,
            "edge_start": edge_start,
            "edge_end": edge_end,
            "edges": batch_edges,
            "accepted": power_step.accepted,
            "error_occurred": bool(
                power_step.info.get("error_occurred")
            ),
            "result": str(power_step.info.get("result", ""))[:1800],
            "policy_execution_time": power_step.info.get(
                "policy_execution_time"
            ),
            "ticks": power_step.info.get("ticks"),
        }
        power_batches.append(batch_diag)
        journal.state["metrics"]["electric_mining_power_batches"] = (
            power_batches
        )
        journal.flush()
        if not power_step.accepted:
            return fail_transition(
                phase="power_topology",
                detail=(
                    "Electric transition shared power-backbone batch "
                    f"{len(power_batches)} failed."
                ),
                event_message="Electric MST power-topology transaction rejected.",
                diagnostics={
                    **route_buffer_diagnostics,
                    "construction_materials": materials_diagnostics,
                    "power_batches": power_batches,
                    "result": batch_diag["result"],
                },
            )

    # Phase E — each material route is its own transaction.  A path failure
    # cannot roll back successful independent routes, and diagnostics identify
    # the exact topology that failed.
    belt_route_specs = (
        (
            "coal_to_boiler",
            "electric_coal_drill",
            "boiler_inserter",
            "autonomy_coal_belts",
        ),
        (
            "iron_ore",
            "electric_iron_drill",
            "iron_ore_inserter",
            "autonomy_iron_ore_belts",
        ),
        (
            "iron_fuel",
            "iron_fuel_drill",
            "iron_fuel_inserter",
            "autonomy_iron_fuel_belts",
        ),
        (
            "copper_ore",
            "electric_copper_drill",
            "copper_ore_inserter",
            "autonomy_copper_ore_belts",
        ),
        (
            "copper_fuel",
            "copper_fuel_drill",
            "copper_fuel_inserter",
            "autonomy_copper_fuel_belts",
        ),
    )
    belt_routes: list[dict[str, Any]] = []
    for route_name, source_name, target_name, count_name in belt_route_specs:
        belt_code = (
            f"{route_name}_connection=connect_entities(\n"
            f"    {source_name},\n"
            f"    {target_name},\n"
            "    Prototype.TransportBelt,\n"
            ")\n"
            f"{count_name}=len(getattr({route_name}_connection,'belts',[]))\n"
            f"print({{'route':'{route_name}','belts':{count_name}}})"
        )
        belt_step = executor.execute(
            belt_code,
            accept=lambda result, metric=count_name: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
                and float(getattr(namespace, metric, 0) or 0) > 0
            ),
            use_checkpoint_for_action=False,
        )
        route_count = int(getattr(namespace, count_name, 0) or 0)
        route_diag = {
            "route": route_name,
            "source": source_name,
            "target": target_name,
            "accepted": belt_step.accepted,
            "error_occurred": bool(
                belt_step.info.get("error_occurred")
            ),
            "result": str(belt_step.info.get("result", ""))[:1800],
            "policy_execution_time": belt_step.info.get(
                "policy_execution_time"
            ),
            "ticks": belt_step.info.get("ticks"),
            "belt_count": route_count,
        }
        belt_routes.append(route_diag)
        journal.state["metrics"]["electric_mining_belt_routes"] = belt_routes
        journal.flush()
        if not belt_step.accepted:
            return fail_transition(
                phase="belt_topology",
                detail=f"Electric transition belt route {route_name} failed.",
                event_message="Electric belt-topology transaction rejected.",
                diagnostics={
                    **route_buffer_diagnostics,
                    "construction_materials": materials_diagnostics,
                    "power_batches": power_batches,
                    "belt_routes": belt_routes,
                    "result": route_diag["result"],
                },
            )

    # Phase F — production evidence begins only after every manually loaded
    # primary-furnace input/fuel stack was cleared and all physical routes were
    # connected.  This removes residual-WIP confounding from the production
    # deltas.
    iron_before = _production_counter(namespace, "iron-plate")
    copper_before = _production_counter(namespace, "copper-plate")
    coal_before = _production_counter(namespace, "coal")

    soak_step = executor.execute(
        """sleep(120)
electric_coal_drill=get_entity(
    Prototype.ElectricMiningDrill,
    electric_coal_drill.position,
)
electric_iron_drill=get_entity(
    Prototype.ElectricMiningDrill,
    electric_iron_drill.position,
)
electric_copper_drill=get_entity(
    Prototype.ElectricMiningDrill,
    electric_copper_drill.position,
)
boiler=get_entity(Prototype.Boiler,boiler.position)
steam_engine=get_entity(Prototype.SteamEngine,steam_engine.position)
iron_furnace=get_entity(Prototype.StoneFurnace,iron_furnace.position)
copper_furnace=get_entity(Prototype.StoneFurnace,copper_furnace.position)

electric_coal_status=str(electric_coal_drill.status)
electric_iron_status=str(electric_iron_drill.status)
electric_copper_status=str(electric_copper_drill.status)
autonomy_boiler_status=str(boiler.status)
autonomy_engine_status=str(steam_engine.status)
autonomy_iron_furnace_status=str(iron_furnace.status)
autonomy_copper_furnace_status=str(copper_furnace.status)
autonomy_iron_output=float(
    inspect_inventory(iron_output_chest)[Prototype.IronPlate]
)
autonomy_copper_output=float(
    inspect_inventory(copper_output_chest)[Prototype.CopperPlate]
)
print({
  'electric_coal_status':electric_coal_status,
  'electric_iron_status':electric_iron_status,
  'electric_copper_status':electric_copper_status,
  'boiler_status':autonomy_boiler_status,
  'engine_status':autonomy_engine_status,
  'iron_furnace_status':autonomy_iron_furnace_status,
  'copper_furnace_status':autonomy_copper_furnace_status,
  'iron_output':autonomy_iron_output,
  'copper_output':autonomy_copper_output,
})""",
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
    )

    iron_after = _production_counter(namespace, "iron-plate")
    copper_after = _production_counter(namespace, "copper-plate")
    coal_after = _production_counter(namespace, "coal")
    measured = {
        "iron_delta": max(0.0, iron_after - iron_before),
        "copper_delta": max(0.0, copper_after - copper_before),
        "coal_delta": max(0.0, coal_after - coal_before),
    }
    statuses = {
        "electric_coal_status": str(
            getattr(namespace, "electric_coal_status", "")
        ),
        "electric_iron_status": str(
            getattr(namespace, "electric_iron_status", "")
        ),
        "electric_copper_status": str(
            getattr(namespace, "electric_copper_status", "")
        ),
        "boiler_status": str(
            getattr(namespace, "autonomy_boiler_status", "")
        ),
        "engine_status": str(
            getattr(namespace, "autonomy_engine_status", "")
        ),
        "iron_furnace_status": str(
            getattr(namespace, "autonomy_iron_furnace_status", "")
        ),
        "copper_furnace_status": str(
            getattr(namespace, "autonomy_copper_furnace_status", "")
        ),
    }
    output_buffers = {
        "iron": float(
            getattr(namespace, "autonomy_iron_output", 0) or 0
        ),
        "copper": float(
            getattr(namespace, "autonomy_copper_output", 0) or 0
        ),
    }
    severe_markers = (
        "NO_POWER",
        "NOT_CONNECTED",
        "NOT_PLUGGED",
    )
    power_healthy = all(
        not any(marker in status for marker in severe_markers)
        for status in (
            statuses["electric_coal_status"],
            statuses["electric_iron_status"],
            statuses["electric_copper_status"],
            statuses["engine_status"],
        )
    )
    fuel_healthy = "NO_FUEL" not in statuses["boiler_status"]
    all_route_counts_positive = all(
        int(row["belt_count"]) > 0
        for row in belt_routes
    )
    production_positive = all(
        measured[key] > 0
        for key in ("iron_delta", "copper_delta", "coal_delta")
    )
    output_distribution = (
        output_buffers["iron"] > 0
        and output_buffers["copper"] > 0
    )

    soak_diagnostics = {
        "accepted": soak_step.accepted,
        "error_occurred": bool(soak_step.info.get("error_occurred")),
        "result": str(soak_step.info.get("result", ""))[:2200],
        "policy_execution_time": soak_step.info.get("policy_execution_time"),
        "ticks": soak_step.info.get("ticks"),
        "statuses": statuses,
        "output_buffers": output_buffers,
        "power_healthy": power_healthy,
        "fuel_healthy": fuel_healthy,
        "all_route_counts_positive": all_route_counts_positive,
        "production_positive": production_positive,
        "output_distribution": output_distribution,
        **measured,
    }
    journal.state["metrics"]["electric_mining_backbone_soak"] = (
        soak_diagnostics
    )

    diagnostics = {
        "phase": "physical_backbone",
        "accepted": bool(
            soak_step.accepted
            and power_healthy
            and fuel_healthy
            and all_route_counts_positive
            and production_positive
            and output_distribution
        ),
        "error_occurred": bool(soak_step.info.get("error_occurred")),
        "result": soak_diagnostics["result"],
        "researched": True,
        "belt_required": layout_diagnostics["belt_required"],
        "pole_required": layout_diagnostics["pole_required"],
        "belt_requirement_estimates": layout_diagnostics[
            "belt_requirement_estimates"
        ],
        "pole_requirement_estimates": layout_diagnostics[
            "pole_requirement_estimates"
        ],
        "route_iron": route_iron,
        "route_iron_target": route_iron_target,
        "route_copper": route_copper,
        "route_copper_target": route_copper_target,
        "route_wood": route_wood,
        "route_wood_target": route_wood_target,
        "route_buffer_rounds": route_buffer_rounds,
        "route_buffer_max_rounds": route_buffer_max_rounds,
        "commissioning_coal": layout_diagnostics["commissioning_coal"],
        "layout_variant": layout_variant,
        "commissioning_coal_target": commissioning_coal_target,
        "belt_margin": belt_margin,
        "pole_margin": pole_margin,
        "route_detour_margin": route_detour_margin,
        "layout": layout_diagnostics["layout"],
        "route_buffer_batches": route_batches,
        "construction_materials": materials_diagnostics,
        "power_batches": power_batches,
        "belt_routes": belt_routes,
        "backbone_soak": soak_diagnostics,
        **statuses,
        **measured,
        "material_buffer": buffer_diagnostics,
    }
    journal.state["metrics"]["electric_mining_transition"] = diagnostics
    journal.flush()

    if not diagnostics["accepted"]:
        return fail_transition(
            phase="physical_backbone",
            detail=(
                "Electric transition failed the post-WIP physical production "
                "gate."
            ),
            event_message="Electric physical production backbone rejected.",
            diagnostics=diagnostics,
        )

    journal.complete_stage(
        STAGE_ELECTRIC_MINING_TRANSITION,
        (
            "Electric coal→boiler, iron→furnace and copper→furnace "
            "belt/inserter loops produced live output after residual WIP "
            "was cleared."
        ),
    )
    journal.event(
        "autonomy",
        "Physical electric production backbone commissioned.",
        diagnostics=diagnostics,
    )
    return True


def _autonomy_soak(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int = 120,
) -> bool:
    instance = env.unwrapped.instance
    namespace = instance.namespace
    journal.set_stage(
        STAGE_AUTONOMY_SOAK,
        status="validating",
        detail=(
            "Free-running the factory with no harvest/insert/extract calls; "
            "measuring live topology, starvation and autonomous throughput."
        ),
        next_action="accept only a physically closed-loop survivor",
    )

    baseline = executor.intervention_snapshot()
    items = (
        "coal",
        "iron-ore",
        "iron-plate",
        "copper-ore",
        "copper-plate",
        "automation-science-pack",
        "logistic-science-pack",
    )
    before = {
        item: _production_counter(namespace, item)
        for item in items
    }

    step = executor.execute(
        f"sleep({max(60, settle_seconds)})\nprint('autonomy soak complete')",
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
    )
    after = {
        item: _production_counter(namespace, item)
        for item in items
    }
    runtime = float(max(60, settle_seconds))
    rates = {
        item: max(0.0, after[item] - before[item]) / runtime
        for item in items
    }

    intervention_state = _record_interventions(
        executor,
        journal,
        bootstrap_baseline=journal.state.get(
            "_bootstrap_intervention_baseline"
        ),
        autonomy_baseline=baseline,
        assisted_navigation_count=int(
            journal.state["metrics"]
            .get("open_play_bootstrap", {})
            .get("assisted_navigation_count", 0)
            or 0
        ),
    )
    autonomy_window = intervention_state.get(
        "autonomy_window_committed",
        {},
    )
    entities = _autonomy_entity_snapshot(instance)
    report = evaluate_factory_autonomy(
        entities=entities,
        # None, not {}: an empty dict reads as "instrumented and nothing
        # carried by hand", which scores as perfect autonomy. Absence of the
        # intervention window means it was never measured.
        interventions=autonomy_window
        if isinstance(autonomy_window, dict)
        else None,
        production_rates_per_s=rates,
        soak_runtime_s=runtime,
        assisted_navigation_count=int(
            intervention_state.get("assisted_navigation_count", 0) or 0
        ),
    )
    payload = report.to_dict()
    payload["production_before"] = before
    payload["production_after"] = after
    payload["intervention_window"] = autonomy_window
    journal.state["metrics"]["autonomy"] = payload
    journal.state["metrics"]["autonomy_soak_runtime_s"] = runtime

    if not step.accepted or not report.closed_loop:
        failed = [
            key
            for key, passed in report.topology.items()
            if not passed
        ]
        journal.fail_stage(
            STAGE_AUTONOMY_SOAK,
            "Autonomy soak rejected: " + ", ".join(failed),
        )
        journal.event(
            "counterexample",
            "Factory failed physical closed-loop autonomy gate.",
            autonomy=payload,
            failed_gates=failed,
        )
        return False

    journal.state["engineering_progression"]["achieved"].extend(
        sorted(report.capabilities)
    )
    journal.complete_stage(
        STAGE_AUTONOMY_SOAK,
        "Closed-loop factory survived the zero-intervention autonomy soak.",
    )
    journal.event(
        "autonomy",
        "Closed-loop physical autonomy validated.",
        autonomy=payload,
    )
    return True


def run_open_play_validation(
    *,
    seed: int,
    smelt_seconds: int,
    coal_seconds: int,
    power_seconds: int,
    research_seconds: int,
    assembler_seconds: int,
    configuration_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import gym

    champion = read_json_object(EVOLUTION_CHAMPION)
    if not champion:
        raise RuntimeError(
            "No experimental champion exists yet; open-play validation is gated."
        )

    validation_champion = dict(champion)
    validation_configuration = dict(champion.get("configuration", {}))
    if configuration_override:
        validation_configuration.update(configuration_override)
    validation_champion["configuration"] = validation_configuration

    list_environments()
    env = gym.make("open_play", run_idx=0)
    fle_eval_timeout_s = enforce_minimum_eval_timeout(
        env,
        minimum_seconds=300,
    )
    run_id = datetime.now(UTC).strftime("open-play-%Y%m%dT%H%M%SZ")
    journal = _prepare_journal(run_id, validation_champion)
    executor = TransactionalFLEExecutor(
        env,
        runtime_context=lambda: {
            "run_id": journal.run_id,
            "arena": "open_play",
            "stage": journal.state.get("stage"),
            "run_status": journal.state.get("status"),
            "progress": journal.state.get("progress"),
        },
    )
    world_lease = FactorioWorldLease(
        run_id=run_id,
        arena="open_play",
        owner="open_play_runner",
    ).acquire()
    journal.state["evolution"]["fle_eval_timeout_s"] = fle_eval_timeout_s
    journal.state["evolution"]["lab_champion_configuration"] = champion.get(
        "configuration",
        {},
    )
    journal.state["evolution"]["validation_configuration"] = (
        validation_configuration
    )
    journal.flush()

    try:
        executor.reset(seed=seed)
        journal.event(
            "checkpoint",
            "Open-play world reset with empty inventory and real technology tree.",
            seed=seed,
            experimental_champion=champion.get("run_id"),
        )

        ok = _bootstrap_raw(executor, env, journal)
        bootstrap_interventions = executor.intervention_snapshot()
        journal.state["_bootstrap_intervention_baseline"] = (
            bootstrap_interventions
        )
        assisted_navigation_count = int(
            journal.state["metrics"]
            .get("open_play_bootstrap", {})
            .get("assisted_navigation_count", 0)
            or 0
        )

        def refresh_interventions() -> None:
            _record_interventions(
                executor,
                journal,
                bootstrap_baseline=bootstrap_interventions,
                assisted_navigation_count=assisted_navigation_count,
            )

        refresh_interventions()
        if ok:
            ok = _technology_triggers(
                executor,
                env,
                journal,
                settle_seconds=smelt_seconds,
            )
            refresh_interventions()
        if ok:
            ok = _coal_survival(
                executor,
                env,
                journal,
                settle_seconds=coal_seconds,
            )
            refresh_interventions()
        if ok:
            ok = _steam_power(
                executor,
                env,
                journal,
                settle_seconds=power_seconds,
            )
            refresh_interventions()
        if ok:
            ok = _lab_and_automation(
                executor,
                env,
                journal,
                settle_seconds=research_seconds,
            )
            refresh_interventions()
        if ok:
            ok = _powered_red_science(
                executor,
                env,
                journal,
                settle_seconds=assembler_seconds,
            )
            refresh_interventions()
        if ok:
            ok = _electric_mining_transition(
                executor,
                env,
                journal,
                research_seconds=research_seconds,
            )
            refresh_interventions()
        if ok:
            ok = _unlock_logistic_science(
                executor,
                env,
                journal,
                settle_seconds=max(180, research_seconds),
            )
            refresh_interventions()
        if ok:
            ok = _green_science_industry(
                executor,
                env,
                journal,
                settle_seconds=max(60, assembler_seconds),
            )
            refresh_interventions()
        if ok:
            ok = _research_logistics(
                executor,
                env,
                journal,
                settle_seconds=max(90, research_seconds),
            )
            refresh_interventions()
        if ok:
            ok = _autonomy_soak(
                executor,
                env,
                journal,
                settle_seconds=max(120, assembler_seconds),
            )

        refresh_interventions()

        journal.state.pop("_bootstrap_intervention_baseline", None)

        metrics = journal.state.get("metrics", {})
        autonomy_metrics = metrics.get("autonomy", {})
        passed = _closed_loop_validation_passed(
            bool(ok),
            metrics if isinstance(metrics, dict) else {},
        )
        if ok and not passed:
            journal.event(
                "failure",
                "Validation pipeline returned success without closed-loop autonomy evidence.",
                autonomy=autonomy_metrics,
            )

        checkpoint_record: dict[str, Any] | None = None
        checkpoint_error: str | None = None
        if passed and executor.game_state is not None:
            try:
                checkpoint = save_game_state(
                    OPEN_PLAY_CHECKPOINTS / f"{run_id}.json",
                    executor.game_state,
                    run_id=run_id,
                    arena="open_play",
                    qualified=False,
                )
                checkpoint_record = checkpoint.to_dict()
                journal.event(
                    "checkpoint",
                    "Durable open-play survivor state persisted.",
                    checkpoint=checkpoint_record,
                )
            except (OSError, TypeError, ValueError) as exc:
                checkpoint_error = f"{type(exc).__name__}: {exc}"
                journal.event(
                    "warning",
                    "Open-play survivor passed but durable checkpoint failed.",
                    error=checkpoint_error,
                )

        record = {
            "at": utc_now(),
            "run_id": run_id,
            "seed": seed,
            "experimental_champion": champion.get("run_id"),
            "experimental_generation": champion.get("generation"),
            "configuration": validation_configuration,
            "lab_champion_configuration": champion.get("configuration", {}),
            "passed": passed,
            "metrics": journal.state.get("metrics", {}),
            "checkpoint": checkpoint_record,
            "checkpoint_error": checkpoint_error,
        }
        robustness = OpenPlayRobustnessGate(
            OPEN_PLAY_ROBUSTNESS_STATE,
            required_passes=3,
        ).record(
            champion_run_id=str(champion.get("run_id") or "unknown"),
            configuration=validation_configuration,
            run_id=run_id,
            seed=seed,
            passed=passed,
            autonomy_runtime_s=float(
                metrics.get("autonomy_soak_runtime_s", 0.0) or 0.0
            )
            if isinstance(metrics, dict)
            else 0.0,
        )
        record["robustness"] = robustness
        append_jsonl(OPEN_PLAY_HISTORY, record)

        if passed and robustness.get("qualified"):
            lifelong_checkpoint: dict[str, Any] | None = None
            lifelong_checkpoint_error: str | None = None
            if executor.game_state is not None:
                try:
                    lifelong_checkpoint = save_game_state(
                        LIFELONG_CHECKPOINT,
                        executor.game_state,
                        run_id=run_id,
                        arena="lifelong_root",
                        qualified=True,
                    ).to_dict()
                    record["lifelong_checkpoint"] = lifelong_checkpoint
                except (OSError, TypeError, ValueError) as exc:
                    lifelong_checkpoint_error = (
                        f"{type(exc).__name__}: {exc}"
                    )
                    journal.event(
                        "warning",
                        "Champion qualified but lifelong checkpoint failed.",
                        error=lifelong_checkpoint_error,
                    )
            validated = {
                **champion,
                "open_play_configuration": validation_configuration,
                "open_play_validation": record,
                "open_play_robustness": robustness,
                "validation_status": "validated",
                "lifelong_checkpoint": lifelong_checkpoint,
                "lifelong_checkpoint_error": lifelong_checkpoint_error,
            }
            atomic_json(VALIDATED_CHAMPION, validated)
            journal.state["evolution"]["promotion"] = {
                "promoted": True,
                "reason": (
                    "Lab champion survived the real technology tree, "
                    "closed-loop autonomy and the multi-seed robustness gate."
                ),
                "regressions": [],
                "improvements": [
                    "open_play_validated",
                    "green_science",
                    "logistics_researched",
                    "closed_loop_autonomy",
                    "multi_seed_survival",
                ],
                "retention_ratio": 0.80,
            }
            journal.state["evolution"]["champion"] = validated
            journal.state["evolution"]["challenger"]["status"] = "validated"
            journal.finish(
                "generation_complete",
                "validated autonomous champion · frontier: throughput and layout optimization",
            )
        elif passed:
            passes = int(robustness.get("distinct_pass_seed_count", 0) or 0)
            required = int(robustness.get("required_passes", 3) or 3)
            journal.state["evolution"]["promotion"] = {
                "promoted": False,
                "reason": (
                    "Open-play pass accepted; multi-seed survival qualification "
                    f"is still pending ({passes}/{required})."
                ),
                "regressions": [],
                "improvements": [
                    "open_play_pass",
                    "closed_loop_autonomy",
                ],
                "retention_ratio": 0.80,
            }
            journal.state["evolution"]["challenger"]["status"] = (
                "robustness_pending"
            )
            journal.finish(
                "validation_pass",
                (
                    f"robustness qualification pending · "
                    f"{passes}/{required} distinct seeds passed"
                ),
            )
        else:
            journal.state["evolution"]["promotion"] = {
                "promoted": False,
                "reason": "Experimental champion failed open-play validation.",
                "regressions": ["open_play_validation_failed"],
                "improvements": [],
                "retention_ratio": 0.80,
            }
            journal.state["evolution"]["challenger"]["status"] = "rejected"
            journal.finish(
                "partial_success",
                "return to lab arena and evolve a new challenger",
            )
        return journal.state
    except Exception as exc:
        journal.state["status"] = "error"
        journal.state["detail"] = f"{type(exc).__name__}: {exc}"
        journal.event(
            "failure",
            "Open-play validation stopped on an exception.",
            error=str(exc),
        )
        journal.finish(
            "error",
            "repair open-play counterexample before retrying validation",
        )
        raise
    finally:
        executor.close()
        world_lease.release()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument("--smelt-seconds", type=int, default=150)
    parser.add_argument("--coal-seconds", type=int, default=28)
    parser.add_argument("--power-seconds", type=int, default=16)
    parser.add_argument("--research-seconds", type=int, default=120)
    parser.add_argument("--assembler-seconds", type=int, default=50)
    args = parser.parse_args()

    result = run_open_play_validation(
        seed=args.seed,
        smelt_seconds=args.smelt_seconds,
        coal_seconds=args.coal_seconds,
        power_seconds=args.power_seconds,
        research_seconds=args.research_seconds,
        assembler_seconds=args.assembler_seconds,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
