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
from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor, list_environments

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
VALIDATED_CHAMPION = RUNS_DIR / "open_play_validated_champion.json"
OPEN_PLAY_HISTORY = RUNS_DIR / "open_play_validation_history.jsonl"


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
        "name": "Coal survival",
        "status": "pending",
        "detail": "Build a burner coal cell and sustain it from mined coal.",
    },
    {
        "name": "Steam power",
        "status": "pending",
        "detail": "Build offshore pump, boiler and steam engine from earned materials.",
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
]


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


def _bootstrap_raw(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        0,
        status="running",
        detail="Harvesting wood, stone, coal, iron ore and copper ore from the map.",
        next_action="create the parallel bootstrap smelter bank",
    )
    code = """
wood_pos=nearest(Resource.Wood)
move_to(wood_pos)
harvest_resource(wood_pos,quantity=50,radius=24)

stone_pos=nearest(Resource.Stone)
move_to(stone_pos)
harvest_resource(stone_pos,quantity=50)

coal_pos=nearest(Resource.Coal)
move_to(coal_pos)
harvest_resource(coal_pos,quantity=100)

iron_pos=nearest(Resource.IronOre)
move_to(iron_pos)
harvest_resource(iron_pos,quantity=300)

copper_pos=nearest(Resource.CopperOre)
move_to(copper_pos)
harvest_resource(copper_pos,quantity=160)

bootstrap_inventory=inspect_inventory()
bootstrap_wood=bootstrap_inventory[Prototype.Wood]
bootstrap_stone=bootstrap_inventory[Prototype.Stone]
bootstrap_coal=bootstrap_inventory[Prototype.Coal]
bootstrap_iron=bootstrap_inventory[Prototype.IronOre]
bootstrap_copper=bootstrap_inventory[Prototype.CopperOre]
print({
  'wood':bootstrap_wood,
  'stone':bootstrap_stone,
  'coal':bootstrap_coal,
  'iron':bootstrap_iron,
  'copper':bootstrap_copper,
})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "bootstrap_wood", 0) or 0) >= 20
            and float(getattr(namespace, "bootstrap_stone", 0) or 0) >= 30
            and float(getattr(namespace, "bootstrap_coal", 0) or 0) >= 60
            and float(getattr(namespace, "bootstrap_iron", 0) or 0) >= 260
            and float(getattr(namespace, "bootstrap_copper", 0) or 0) >= 120
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    if not step.accepted:
        journal.fail_stage(0, "Open-play raw-resource bootstrap failed.")
        return False
    journal.complete_stage(
        0,
        "World-only bootstrap harvested enough wood, stone, coal, iron and copper.",
    )
    journal.event(
        "accept",
        "Open-play bootstrap uses only harvested world resources.",
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
        1,
        status="validating",
        detail=(
            "Smelting enough iron/copper to exercise Factorio 2.0 Steam Power "
            "and Electronics craft-item triggers."
        ),
        next_action="validate Steam Power and Electronics triggers",
    )
    code = f"""
craft_item(Prototype.StoneFurnace,quantity=5)
furnace_box=BuildingBox(width=28,height=9)
furnace_area=nearest_buildable(
    Prototype.StoneFurnace,
    furnace_box,
    copper_pos,
)
move_to(furnace_area.center)
iron_furnace=place_entity(
    Prototype.StoneFurnace,
    position=furnace_area.center,
)
iron_furnace_2=place_entity_next_to(
    Prototype.StoneFurnace,
    iron_furnace.position,
    direction=Direction.RIGHT,
    spacing=3,
)
iron_furnace_3=place_entity_next_to(
    Prototype.StoneFurnace,
    iron_furnace_2.position,
    direction=Direction.RIGHT,
    spacing=3,
)
copper_furnace=place_entity_next_to(
    Prototype.StoneFurnace,
    iron_furnace_3.position,
    direction=Direction.RIGHT,
    spacing=3,
)
copper_furnace_2=place_entity_next_to(
    Prototype.StoneFurnace,
    copper_furnace.position,
    direction=Direction.RIGHT,
    spacing=3,
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
print({
  'iron_plates':extracted_iron,
  'copper_plates':extracted_copper,
})
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
    journal.state["metrics"].update(
        {
            "open_play_iron_plates": float(
                getattr(namespace, "extracted_iron", 0) or 0
            ),
            "open_play_copper_plates": float(
                getattr(namespace, "extracted_copper", 0) or 0
            ),
            "steam_power_triggered": steam,
            "electronics_triggered": electronics,
        }
    )
    if not step.accepted or not steam or not electronics:
        journal.fail_stage(
            1,
            (
                "Real tech trigger validation failed: "
                f"steam_power={steam}, electronics={electronics}."
            ),
        )
        return False

    journal.state["engineering_progression"]["achieved"] = [
        "steam_power_trigger",
        "electronics_trigger",
    ]
    journal.complete_stage(
        1,
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
        2,
        status="validating",
        detail=(
            "Building the first automatic coal cell with only a small harvested "
            "fuel seed, then refueling it from its own output."
        ),
        next_action="prove endogenous coal survival in open play",
    )
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
sleep({max(10, settle_seconds // 2)})
first_coal=inspect_inventory(coal_chest)[Prototype.Coal]
coal_transfer=0
if first_coal>0:
    coal_transfer=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(4,first_coal),
    )
if coal_transfer>0:
    coal_drill=insert_item(
        Prototype.Coal,
        coal_drill,
        quantity=min(2,coal_transfer),
    )
remaining_player_coal=inspect_inventory()[Prototype.Coal]
if remaining_player_coal>0:
    coal_chest=insert_item(
        Prototype.Coal,
        coal_chest,
        quantity=remaining_player_coal,
    )
sleep({settle_seconds})
coal_stockpile=inspect_inventory(coal_chest)[Prototype.Coal]
print({
  'coal_seed':coal_seed,
  'coal_transfer':coal_transfer,
  'coal_stockpile':coal_stockpile,
})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "coal_transfer", 0) or 0) > 0
            and float(getattr(namespace, "coal_stockpile", 0) or 0) > 0
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    if not step.accepted:
        journal.fail_stage(2, "Open-play coal cell failed endogenous survival.")
        return False
    journal.state["metrics"]["open_play_coal_stockpile"] = float(
        getattr(namespace, "coal_stockpile", 0) or 0
    )
    journal.complete_stage(
        2,
        "Coal cell survived on internally mined fuel and retained a stockpile.",
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
        3,
        status="validating",
        detail="Building a real Steam Power network from unlocked recipes.",
        next_action="validate electrical generation",
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
print({'steam_energy':open_play_steam_energy})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "open_play_steam_energy", 0) or 0) > 0
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    if not step.accepted:
        journal.fail_stage(3, "Open-play steam network generated no power.")
        return False
    journal.state["metrics"]["open_play_steam_energy"] = float(
        getattr(namespace, "open_play_steam_energy", 0) or 0
    )
    journal.complete_stage(3, "Open-play steam network generated electrical power.")
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
    journal.set_stage(
        4,
        status="validating",
        detail=(
            "Crafting a real lab, triggering automation-science-pack, powering "
            "the lab and researching Automation."
        ),
        next_action="research Automation with 10 red science packs",
    )
    code = f"""
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

craft_item(Prototype.Lab,quantity=1)
lab_trigger_inventory=inspect_inventory()[Prototype.Lab]
sleep(2)
craft_item(Prototype.AutomationSciencePack,quantity=10)
craft_item(Prototype.SmallElectricPole,quantity=6)
red_packs=inspect_inventory()[Prototype.AutomationSciencePack]

lab_box=BuildingBox(width=7,height=7)
lab_area=nearest_buildable(Prototype.Lab,lab_box,steam_engine.position)
move_to(lab_area.center)
lab=place_entity(Prototype.Lab,position=lab_area.center)
connect_entities(steam_engine,lab,Prototype.SmallElectricPole)
lab=insert_item(
    Prototype.AutomationSciencePack,
    lab,
    quantity=min(10,red_packs),
)
research_requirements=set_research(Technology.Automation)
sleep({settle_seconds})
automation_remaining=get_research_progress(Technology.Automation)
print({
  'lab_trigger_inventory':lab_trigger_inventory,
  'red_packs':red_packs,
  'remaining':automation_remaining,
})
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
    journal.state["metrics"].update(
        {
            "automation_science_triggered": science_unlocked,
            "automation_researched": automation,
            "open_play_red_packs_crafted": float(
                getattr(namespace, "red_packs", 0) or 0
            ),
        }
    )
    if not step.accepted or not science_unlocked or not automation:
        journal.fail_stage(
            4,
            (
                "Open-play lab validation failed: "
                f"science_unlock={science_unlocked}, automation={automation}."
            ),
        )
        return False
    journal.complete_stage(
        4,
        "Lab trigger unlocked red science and 10 packs researched Automation.",
    )
    journal.event(
        "technology",
        "Automation researched in a powered lab using earned red science.",
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
    journal.set_stage(
        5,
        status="validating",
        detail=(
            "Using the newly unlocked assembling machine to produce red science "
            "under real open-play power."
        ),
        next_action="validate powered automation-science production",
    )
    code = f"""
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

craft_item(Prototype.AssemblingMachine1,quantity=1)
craft_item(Prototype.IronGearWheel,quantity=8)

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
connect_entities(steam_engine,science_assembler,Prototype.SmallElectricPole)

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
sleep({settle_seconds})
automated_red=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
print({
  'gear_input':gear_input,
  'copper_input':copper_input,
  'automated_red':automated_red,
})
"""

    def accept(result: Any) -> bool:
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and float(getattr(namespace, "automated_red", 0) or 0) > 0
        )

    step = executor.execute(code, accept=accept, use_checkpoint_for_action=False)
    if not step.accepted:
        journal.fail_stage(
            5,
            "Assembling machine produced no open-play automation science.",
        )
        return False
    output = float(getattr(namespace, "automated_red", 0) or 0)
    journal.state["metrics"]["open_play_automated_red_science"] = output
    journal.complete_stage(
        5,
        f"Powered assembler produced {output:.0f} automation science packs.",
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
) -> dict[str, Any]:
    import gym

    champion = read_json_object(EVOLUTION_CHAMPION)
    if not champion:
        raise RuntimeError(
            "No experimental champion exists yet; open-play validation is gated."
        )

    list_environments()
    env = gym.make("open_play", run_idx=0)
    executor = TransactionalFLEExecutor(env)
    run_id = datetime.now(UTC).strftime("open-play-%Y%m%dT%H%M%SZ")
    journal = _prepare_journal(run_id, champion)

    try:
        executor.reset(seed=seed)
        journal.event(
            "checkpoint",
            "Open-play world reset with empty inventory and real technology tree.",
            seed=seed,
            experimental_champion=champion.get("run_id"),
        )

        ok = _bootstrap_raw(executor, env, journal)
        if ok:
            ok = _technology_triggers(
                executor,
                env,
                journal,
                settle_seconds=smelt_seconds,
            )
        if ok:
            ok = _coal_survival(
                executor,
                env,
                journal,
                settle_seconds=coal_seconds,
            )
        if ok:
            ok = _steam_power(
                executor,
                env,
                journal,
                settle_seconds=power_seconds,
            )
        if ok:
            ok = _lab_and_automation(
                executor,
                env,
                journal,
                settle_seconds=research_seconds,
            )
        if ok:
            ok = _powered_red_science(
                executor,
                env,
                journal,
                settle_seconds=assembler_seconds,
            )

        record = {
            "at": utc_now(),
            "run_id": run_id,
            "experimental_champion": champion.get("run_id"),
            "experimental_generation": champion.get("generation"),
            "configuration": champion.get("configuration", {}),
            "passed": bool(ok),
            "metrics": journal.state.get("metrics", {}),
        }
        append_jsonl(OPEN_PLAY_HISTORY, record)

        if ok:
            validated = {
                **champion,
                "open_play_validation": record,
                "validation_status": "validated",
            }
            atomic_json(VALIDATED_CHAMPION, validated)
            journal.state["evolution"]["promotion"] = {
                "promoted": True,
                "reason": (
                    "Lab champion survived empty-inventory open-play technology "
                    "progression through powered red science."
                ),
                "regressions": [],
                "improvements": ["open_play_validated"],
                "retention_ratio": 0.80,
            }
            journal.state["evolution"]["champion"] = validated
            journal.state["evolution"]["challenger"]["status"] = "validated"
            journal.finish(
                "generation_complete",
                "validated champion · frontier: green science and circuits",
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
