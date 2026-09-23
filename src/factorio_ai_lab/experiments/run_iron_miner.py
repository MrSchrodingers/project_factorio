from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.experiments.curriculum_runner import _step_error_text
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    fast_reposition,
    list_environments,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
ACTIVE_RUN = RUNS_DIR / "active_run.json"
RUN_HISTORY_DIR = RUNS_DIR / "construction"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _write_run(record: dict[str, Any]) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_RUN.write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _finish_run(record: dict[str, Any]) -> None:
    _write_run(record)
    RUN_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    history_path = RUN_HISTORY_DIR / f"{record['run_id']}.json"
    history_path.write_text(
        json.dumps(record, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _patch_center(patch: Any) -> tuple[float, float]:
    box = patch.bounding_box
    x = (float(box.left_top.x) + float(box.right_bottom.x)) / 2.0
    y = (float(box.left_top.y) + float(box.right_bottom.y)) / 2.0
    return x, y


def run(seed: int, settle_seconds: int) -> dict[str, Any]:
    import gym

    list_environments()
    env = gym.make("iron_ore_throughput", run_idx=0)
    executor = TransactionalFLEExecutor(env)

    run_id = datetime.now(UTC).strftime("iron-miner-%Y%m%dT%H%M%SZ")
    record: dict[str, Any] = {
        "run_id": run_id,
        "objective": "Mine iron ore autonomously into a wooden chest",
        "environment": "iron_ore_throughput",
        "seed": seed,
        "status": "starting",
        "stage": "reset",
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "plan": [
            "reset deterministic lab environment",
            "discover nearest iron resource patch",
            "select geometric patch center",
            "fast reposition synthetic FLE agent",
            "place and fuel burner mining drill",
            "place wooden chest at drill drop position",
            "wait for production",
            "accept only if reward and production are positive",
        ],
        "metrics": {},
        "events": [],
    }
    _write_run(record)

    try:
        executor.reset(seed=seed)
        instance = env.unwrapped.instance
        namespace = instance.namespace

        record["status"] = "running"
        record["stage"] = "world_discovery"
        record["updated_at"] = _utc_now()
        _write_run(record)

        perception = executor.execute(
            '''
iron = nearest(Resource.IronOre)
patch = get_resource_patch(Resource.IronOre, iron, radius=30)
print({'iron': iron, 'patch': patch})
''',
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
            ),
        )
        if not perception.accepted:
            raise RuntimeError("world discovery checkpoint was rejected")

        nearest_iron = namespace.iron
        patch = namespace.patch
        if patch is None:
            raise RuntimeError("no iron resource patch found")

        target_x, target_y = _patch_center(patch)
        reposition_distance = math.hypot(target_x, target_y)

        record["world"] = {
            "nearest_iron": {"x": nearest_iron.x, "y": nearest_iron.y},
            "patch_size": patch.size,
            "patch_center": {"x": target_x, "y": target_y},
            "patch_bounds": {
                "left_top": {
                    "x": patch.bounding_box.left_top.x,
                    "y": patch.bounding_box.left_top.y,
                },
                "right_bottom": {
                    "x": patch.bounding_box.right_bottom.x,
                    "y": patch.bounding_box.right_bottom.y,
                },
            },
        }
        record["events"].append(
            {
                "at": _utc_now(),
                "type": "world_model",
                "message": "iron patch discovered and center selected",
            }
        )

        record["stage"] = "reposition"
        record["updated_at"] = _utc_now()
        _write_run(record)

        reposition = fast_reposition(env, x=target_x, y=target_y)
        record["events"].append(
            {
                "at": _utc_now(),
                "type": "movement",
                "message": "synthetic agent repositioned in FLE fast mode",
                "x": reposition.x,
                "y": reposition.y,
            }
        )

        record["stage"] = "construction"
        record["updated_at"] = _utc_now()
        _write_run(record)

        code = f"""
drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target_x}, y={target_y}),
    direction=Direction.DOWN,
)
drill = insert_item(Prototype.Coal, drill, quantity=20)
chest = place_entity_next_to(
    Prototype.WoodenChest,
    drill.position,
    direction=Direction.DOWN,
)
print({{'drill': drill, 'chest': chest}})
sleep({settle_seconds})
drill = get_entity(Prototype.BurnerMiningDrill, drill.position)
chest = get_entity(Prototype.WoodenChest, chest.position)
print({{'final_drill': drill, 'chest_inventory': inspect_inventory(chest)}})
"""
        step = executor.execute(
            code,
            accept=lambda result: (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
                and result.reward > 0
            ),
            use_checkpoint_for_action=False,
        )

        record["stage"] = "validation"
        record["updated_at"] = _utc_now()
        record["metrics"].update(
            {
                "reward": step.reward,
                "accepted": step.accepted,
                "terminated": step.terminated,
                "truncated": step.truncated,
                "reposition_distance": reposition_distance,
                "elapsed_ticks": step.info.get("ticks"),
            }
        )

        if not step.accepted:
            record["status"] = "rejected"
            record["stage"] = "rolled_back"
            record["failure"] = {
                "error_occurred": step.info.get("error_occurred"),
                "error": _step_error_text(step.info),
                "result": str(step.info.get("result"))[:4000],
            }
            record["finished_at"] = _utc_now()
            _finish_run(record)
            return record

        production = namespace._get_production_stats()
        entities = namespace._save_entity_state(
            distance=500,
            player_entities=True,
            resource_entities=False,
            items_on_ground=False,
            encode=False,
            compress=False,
        )
        iron_output = float(production.get("output", {}).get("iron-ore", 0))

        if iron_output <= 0:
            record["status"] = "failed_validation"
            record["stage"] = "production_check"
            record["failure"] = {
                "message": "checkpoint produced no measured iron output",
                "production": production,
            }
            record["finished_at"] = _utc_now()
            _finish_run(record)
            return record

        record["metrics"].update(
            {
                "iron_output": iron_output,
                "entity_count": len(entities),
                "production": production,
            }
        )
        record["events"].append(
            {
                "at": _utc_now(),
                "type": "checkpoint",
                "message": "construction accepted after positive iron production",
            }
        )
        record["status"] = "success"
        record["stage"] = "completed"
        record["finished_at"] = _utc_now()
        record["updated_at"] = _utc_now()
        _finish_run(record)
        return record
    except Exception as exc:
        record["status"] = "error"
        record["stage"] = "exception"
        record["failure"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        record["finished_at"] = _utc_now()
        record["updated_at"] = _utc_now()
        _finish_run(record)
        raise
    finally:
        executor.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument("--settle-seconds", type=int, default=20)
    args = parser.parse_args()

    result = run(args.seed, args.settle_seconds)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
