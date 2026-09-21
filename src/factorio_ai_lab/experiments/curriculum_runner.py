from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.agents.llm_router import default_free_router
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    fast_reposition,
    list_environments,
)
from factorio_ai_lab.learning.bandit import UCB1Bandit

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
RESEARCH_STATE = RUNS_DIR / "research_state.json"
KNOWLEDGE_LOG = RUNS_DIR / "knowledge.jsonl"
ACTIVE_RUN = RUNS_DIR / "active_run.json"
RESEARCH_HISTORY = RUNS_DIR / "research"

THROUGHPUT_EQUIVALENCE_TOLERANCE = 1.0


PLACEMENT_ARMS: dict[str, tuple[float, float]] = {
    "east_near": (4.5, 0.0),
    "west_near": (-4.5, 0.0),
    "north_near": (0.0, -5.5),
    "south_near": (0.0, 5.5),
    "northwest_edge": (-9.5, -8.5),
    "southeast_edge": (9.5, 8.5),
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


def patch_center(patch: Any) -> tuple[float, float]:
    box = patch.bounding_box
    return (
        (float(box.left_top.x) + float(box.right_bottom.x)) / 2.0,
        (float(box.left_top.y) + float(box.right_bottom.y)) / 2.0,
    )


class ResearchJournal:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.state: dict[str, Any] = {
            "run_id": run_id,
            "status": "starting",
            "objective": (
                "Grow a validated iron-production factory while learning "
                "placement choices from real Factorio trials"
            ),
            "detail": "Bootstrapping live curriculum.",
            "stage": "bootstrap",
            "progress": 0.0,
            "next_action": "initialize FLE environment",
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "curriculum": [
                {
                    "name": "Baseline iron mining",
                    "status": "pending",
                    "detail": "Build and validate one burner drill feeding a chest.",
                },
                {
                    "name": "Online placement learning",
                    "status": "pending",
                    "detail": "UCB1 tests second-cell positions with rollback.",
                },
                {
                    "name": "Scale mining",
                    "status": "pending",
                    "detail": "Promote the best learned placement into the live factory.",
                },
                {
                    "name": "Smelting probe",
                    "status": "pending",
                    "detail": "Attempt drill-to-furnace iron-plate automation transactionally.",
                },
            ],
            "online_learning": {
                "algorithm": "ucb1_real_factorio_placement",
                "status": "pending",
                "history": [],
                "best_arm": None,
                "arms": {},
            },
            "capabilities": {
                "llm_knowledge": {
                    "status": "active",
                    "model": "qwen3-4b",
                    "role": "post-experiment lesson synthesis",
                },
                "neural_policy": {
                    "status": "not_trained",
                    "planned": "cnn_attention",
                    "reason": "real spatial demonstration dataset still too small",
                },
                "world_model": {
                    "status": "explicit_only",
                    "planned": "residual recurrent model",
                    "reason": "learn residual only after measurable model mismatch exists",
                },
            },
            "metrics": {},
            "events": [],
        }
        self.flush()

    def event(self, event_type: str, message: str, **extra: Any) -> None:
        event = {
            "at": utc_now(),
            "type": event_type,
            "message": message,
            **extra,
        }
        self.state["events"].append(event)
        self.state["events"] = self.state["events"][-120:]
        self.state["updated_at"] = utc_now()
        self.flush()

    def set_stage(
        self,
        index: int,
        *,
        status: str,
        detail: str | None = None,
        next_action: str | None = None,
    ) -> None:
        curriculum = self.state["curriculum"]
        curriculum[index]["status"] = status
        self.state["stage"] = curriculum[index]["name"]
        self.state["current_stage"] = curriculum[index]
        self.state["status"] = "learning" if status == "learning" else "running"
        self.state["progress"] = index / max(len(curriculum), 1)
        if detail is not None:
            self.state["detail"] = detail
        if next_action is not None:
            self.state["next_action"] = next_action
        self.state["updated_at"] = utc_now()
        self.flush()

    def complete_stage(self, index: int, detail: str) -> None:
        self.state["curriculum"][index]["status"] = "completed"
        self.state["curriculum"][index]["result"] = detail
        self.state["progress"] = (index + 1) / len(self.state["curriculum"])
        self.state["detail"] = detail
        self.state["updated_at"] = utc_now()
        self.flush()

    def fail_stage(self, index: int, detail: str) -> None:
        self.state["curriculum"][index]["status"] = "failed"
        self.state["curriculum"][index]["result"] = detail
        self.state["detail"] = detail
        self.state["updated_at"] = utc_now()
        self.flush()

    def finish(self, status: str, next_action: str) -> None:
        self.state["status"] = status
        if status == "completed":
            self.state["progress"] = 1.0
        self.state["next_action"] = next_action
        self.state["finished_at"] = utc_now()
        self.state["updated_at"] = utc_now()
        self.flush()
        RESEARCH_HISTORY.mkdir(parents=True, exist_ok=True)
        atomic_json(RESEARCH_HISTORY / f"{self.run_id}.json", self.state)

    def flush(self) -> None:
        atomic_json(RESEARCH_STATE, self.state)
        active = {
            "run_id": self.run_id,
            "objective": self.state["objective"],
            "status": self.state["status"],
            "stage": self.state["stage"],
            "started_at": self.state["started_at"],
            "updated_at": self.state["updated_at"],
            "progress": self.state["progress"],
            "metrics": self.state["metrics"],
            "events": self.state["events"][-30:],
        }
        atomic_json(ACTIVE_RUN, active)


def synthesize_lesson(
    *,
    stage: str,
    facts: dict[str, Any],
    fallback_lesson: str,
    fallback_hypothesis: str,
) -> dict[str, Any]:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "factorio_research_lesson",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "lesson": {"type": "string"},
                    "next_hypothesis": {"type": "string"},
                },
                "required": ["lesson", "next_hypothesis"],
                "additionalProperties": False,
            },
        },
    }
    lesson = fallback_lesson
    hypothesis = fallback_hypothesis
    provider: dict[str, Any] | None = None

    try:
        result = default_free_router().chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the research notebook for a Factorio AI lab. "
                        "Summarize only evidence in the supplied JSON. "
                        "Do not claim neural training unless the facts show it. "
                        "Keep each field under 35 words."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"stage": stage, "facts": facts},
                        sort_keys=True,
                        default=str,
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=180,
            response_format=schema,
        )
        parsed = json.loads(result["choices"][0]["message"]["content"])
        lesson = parsed["lesson"]
        hypothesis = parsed["next_hypothesis"]
        provider = result.get("_router")
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        provider = {"error": f"{type(exc).__name__}: {exc}"}

    record = {
        "at": utc_now(),
        "stage": stage,
        "lesson": lesson,
        "next_hypothesis": hypothesis,
        "facts": facts,
        "provider": provider,
    }
    append_jsonl(KNOWLEDGE_LOG, record)
    return record


def production_output(namespace: Any, item: str) -> float:
    stats = namespace._get_production_stats()
    return float(stats.get("output", {}).get(item, 0.0))


def stage_baseline(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> tuple[Any, tuple[float, float]]:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        0,
        status="running",
        detail="Discovering iron patch and constructing the first validated cell.",
        next_action="discover iron patch",
    )
    perception = executor.execute(
        """
iron = nearest(Resource.IronOre)
patch = get_resource_patch(Resource.IronOre, iron, radius=30)
print({'iron': iron, 'patch': patch})
""",
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
    )
    if not perception.accepted:
        raise RuntimeError("baseline world perception was rejected")

    patch = namespace.patch
    center = patch_center(patch)
    journal.state["world"] = {
        "nearest_iron": {"x": float(namespace.iron.x), "y": float(namespace.iron.y)},
        "patch_size": patch.size,
        "patch_center": {"x": center[0], "y": center[1]},
        "patch_bounds": {
            "left_top": {
                "x": float(patch.bounding_box.left_top.x),
                "y": float(patch.bounding_box.left_top.y),
            },
            "right_bottom": {
                "x": float(patch.bounding_box.right_bottom.x),
                "y": float(patch.bounding_box.right_bottom.y),
            },
        },
    }
    journal.flush()
    journal.event(
        "world_model",
        "Iron resource patch measured from the live Factorio world.",
        center={"x": center[0], "y": center[1]},
        patch_size=patch.size,
    )

    fast_reposition(env, x=center[0], y=center[1])
    journal.state["next_action"] = "build burner drill and chest"
    journal.flush()

    measurement: dict[str, float] = {}

    def accept_baseline(result: Any) -> bool:
        iron_output = production_output(namespace, "iron-ore")
        measurement["iron_output"] = iron_output
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and iron_output > 0
        )

    code = f"""
drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={center[0]}, y={center[1]}),
    direction=Direction.DOWN,
)
drill = insert_item(Prototype.Coal, drill, quantity=20)
chest = place_entity_next_to(
    Prototype.WoodenChest,
    drill.position,
    direction=Direction.DOWN,
)
sleep({settle_seconds})
print({{'chest_inventory': inspect_inventory(chest)}})
"""
    step = executor.execute(
        code,
        accept=accept_baseline,
        use_checkpoint_for_action=False,
    )
    if not step.accepted:
        raise RuntimeError("baseline mining cell did not produce iron")

    journal.state["metrics"]["baseline_iron_output"] = measurement["iron_output"]
    journal.state["metrics"]["baseline_reward"] = step.reward
    journal.complete_stage(
        0,
        f"Baseline cell accepted with {measurement['iron_output']:.0f} iron ore output.",
    )
    journal.event(
        "accept",
        "Baseline drill-to-chest mining cell accepted.",
        reward=step.reward,
        iron_output=measurement["iron_output"],
    )
    lesson = synthesize_lesson(
        stage="baseline_mining",
        facts={
            "iron_output": measurement["iron_output"],
            "reward": step.reward,
            "entities_added": 2,
            "placement": {"x": center[0], "y": center[1]},
        },
        fallback_lesson=(
            "A burner drill placed inside the measured iron patch and aligned "
            "to a chest produces validated iron output."
        ),
        fallback_hypothesis=(
            "Test a second compact mining cell at several offsets and promote "
            "the best real-world placement."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return patch, center


def stage_online_learning(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    center: tuple[float, float],
    episodes: int,
    settle_seconds: int,
    exploration: float,
) -> str:
    namespace = env.unwrapped.instance.namespace
    bandit = UCB1Bandit(tuple(PLACEMENT_ARMS), exploration=exploration)
    online = journal.state["online_learning"]
    online["status"] = "learning"
    online["history"] = []

    journal.set_stage(
        1,
        status="learning",
        detail="Testing candidate second-miner positions against the same checkpoint.",
        next_action="run transactional placement trials",
    )
    journal.event(
        "learning",
        "Online UCB1 placement learning started on the live Factorio engine.",
        episodes=episodes,
        arms=list(PLACEMENT_ARMS),
    )

    for episode in range(episodes):
        arm = bandit.select()
        dx, dy = PLACEMENT_ARMS[arm]
        target = (center[0] + dx, center[1] + dy)
        fast_reposition(env, x=target[0], y=target[1])

        output_before = production_output(namespace, "iron-ore")
        measured: dict[str, float | bool] = {
            "iron_output": 0.0,
            "iron_output_before": output_before,
            "iron_output_after": output_before,
            "valid": False,
        }

        def reject_trial(
            result: Any,
            measured_state: dict[str, float | bool] = measured,
        ) -> bool:
            output_after = production_output(namespace, "iron-ore")
            iron_delta = max(
                0.0,
                output_after - float(measured_state["iron_output_before"]),
            )
            valid = (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
                and iron_delta > 0
            )
            measured_state["iron_output"] = iron_delta
            measured_state["iron_output_after"] = output_after
            measured_state["valid"] = valid
            return False

        code = f"""
trial_drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target[0]}, y={target[1]}),
    direction=Direction.DOWN,
)
trial_drill = insert_item(Prototype.Coal, trial_drill, quantity=12)
trial_chest = place_entity_next_to(
    Prototype.WoodenChest,
    trial_drill.position,
    direction=Direction.DOWN,
)
sleep({settle_seconds})
print({{'trial_inventory': inspect_inventory(trial_chest)}})
"""
        step = executor.execute(
            code,
            accept=reject_trial,
            use_checkpoint_for_action=False,
        )

        distance = math.hypot(dx, dy)
        if measured["valid"]:
            reward = float(measured["iron_output"])
        else:
            reward = -50.0

        bandit.update(arm, reward)
        row = {
            "episode": episode,
            "arm": arm,
            "offset": {"x": dx, "y": dy},
            "reward": reward,
            "output": float(measured["iron_output"]),
            "output_before": float(measured["iron_output_before"]),
            "output_after": float(measured["iron_output_after"]),
            "valid": bool(measured["valid"]),
            "distance": distance,
            "engine_reward": float(step.reward),
            "at": utc_now(),
        }
        online["history"].append(row)
        online["best_arm"] = bandit.best_observed()
        online["arms"] = {
            key: asdict(value)
            for key, value in bandit.stats().items()
        }
        journal.state["updated_at"] = utc_now()
        journal.flush()
        journal.event(
            "learning",
            f"Placement trial {episode + 1}/{episodes}: {arm}",
            reward=reward,
            output=measured["iron_output"],
            valid=measured["valid"],
        )

    ucb_best = bandit.best_observed()
    output_by_arm: dict[str, list[float]] = {
        arm: []
        for arm in PLACEMENT_ARMS
    }
    for row in online["history"]:
        if row["valid"]:
            output_by_arm[row["arm"]].append(float(row["output"]))
    mean_output_by_arm = {
        arm: sum(values) / len(values)
        for arm, values in output_by_arm.items()
        if values
    }
    max_mean_output = max(mean_output_by_arm.values())
    equivalent_throughput_arms = [
        arm
        for arm, mean_output in mean_output_by_arm.items()
        if max_mean_output - mean_output <= THROUGHPUT_EQUIVALENCE_TOLERANCE
    ]
    min_distance = min(
        math.hypot(*PLACEMENT_ARMS[arm])
        for arm in equivalent_throughput_arms
    )
    compact_candidates = [
        arm
        for arm in equivalent_throughput_arms
        if math.isclose(
            math.hypot(*PLACEMENT_ARMS[arm]),
            min_distance,
            rel_tol=1e-9,
            abs_tol=1e-9,
        )
    ]
    best = next(
        arm
        for arm in PLACEMENT_ARMS
        if arm in compact_candidates
    )

    online["status"] = "learned"
    online["ucb_best_arm"] = ucb_best
    online["best_arm"] = best
    online["equivalent_throughput_arms"] = equivalent_throughput_arms
    online["compact_candidates"] = compact_candidates
    online["mean_output_by_arm"] = mean_output_by_arm
    online["throughput_equivalence_tolerance"] = THROUGHPUT_EQUIVALENCE_TOLERANCE
    journal.state["metrics"]["placement_best_arm"] = best
    journal.state["metrics"]["placement_ucb_best_arm"] = ucb_best
    journal.state["metrics"]["placement_equivalent_throughput_arms"] = (
        equivalent_throughput_arms
    )
    journal.state["metrics"]["placement_compact_candidates"] = compact_candidates
    journal.state["metrics"]["placement_trials"] = episodes
    journal.complete_stage(
        1,
        (
            f"Real trials found throughput-equivalent placements within "
            f"±{THROUGHPUT_EQUIVALENCE_TOLERANCE:.0f} item; "
            f"{best} selected by minimum placement distance."
        ),
    )
    lesson = synthesize_lesson(
        stage="online_placement_learning",
        facts={
            "episodes": episodes,
            "ucb_best_arm": ucb_best,
            "promotion_arm": best,
            "equivalent_throughput_arms": equivalent_throughput_arms,
            "compact_candidates": compact_candidates,
            "mean_output_by_arm": mean_output_by_arm,
            "throughput_equivalence_tolerance": THROUGHPUT_EQUIVALENCE_TOLERANCE,
            "arms": online["arms"],
            "history": online["history"],
        },
        fallback_lesson=(
            "Real Factorio trials found near-equivalent mining throughput "
            f"across {len(equivalent_throughput_arms)} placements; "
            f"{best} was promoted using compactness as the secondary criterion."
        ),
        fallback_hypothesis=(
            "Commit the selected placement, then test whether direct "
            "drill-to-furnace smelting produces iron plates."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return best


def stage_scale_mining(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    center: tuple[float, float],
    best_arm: str,
    settle_seconds: int,
) -> None:
    namespace = env.unwrapped.instance.namespace
    dx, dy = PLACEMENT_ARMS[best_arm]
    target = (center[0] + dx, center[1] + dy)

    journal.set_stage(
        2,
        status="running",
        detail=f"Promoting learned placement {best_arm} into the persistent factory.",
        next_action="commit second mining cell",
    )
    fast_reposition(env, x=target[0], y=target[1])
    output_before = production_output(namespace, "iron-ore")
    measured: dict[str, float] = {}

    def accept_scale(result: Any) -> bool:
        output_after = production_output(namespace, "iron-ore")
        iron_output = max(0.0, output_after - output_before)
        measured["iron_output"] = iron_output
        measured["output_before"] = output_before
        measured["output_after"] = output_after
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and iron_output > 0
        )

    code = f"""
scale_drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target[0]}, y={target[1]}),
    direction=Direction.DOWN,
)
scale_drill = insert_item(Prototype.Coal, scale_drill, quantity=20)
scale_chest = place_entity_next_to(
    Prototype.WoodenChest,
    scale_drill.position,
    direction=Direction.DOWN,
)
sleep({settle_seconds})
print({{'scale_inventory': inspect_inventory(scale_chest)}})
"""
    step = executor.execute(
        code,
        accept=accept_scale,
        use_checkpoint_for_action=False,
    )
    if not step.accepted:
        raise RuntimeError("learned placement failed promotion")

    journal.state["metrics"]["scaled_iron_output"] = measured["iron_output"]
    journal.state["metrics"]["scaled_reward"] = step.reward
    journal.complete_stage(
        2,
        f"Second mining cell committed at {best_arm}; live factory now contains both cells.",
    )
    journal.event(
        "accept",
        "Learned second mining cell promoted to persistent world.",
        arm=best_arm,
        target={"x": target[0], "y": target[1]},
        iron_output=measured["iron_output"],
    )


def stage_smelting_probe(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    center: tuple[float, float],
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    target = (center[0], center[1] - 10.5)

    journal.set_stage(
        3,
        status="validating",
        detail="Testing direct burner-drill-to-furnace smelting with rollback protection.",
        next_action="probe iron-plate automation",
    )
    fast_reposition(env, x=target[0], y=target[1])
    plate_before = production_output(namespace, "iron-plate")
    measured: dict[str, float] = {}

    def validate_smelting(result: Any) -> bool:
        plate_after = production_output(namespace, "iron-plate")
        plates = max(0.0, plate_after - plate_before)
        measured["iron_plate_output"] = plates
        measured["iron_plate_before"] = plate_before
        measured["iron_plate_after"] = plate_after
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and plates > 0
        )

    code = f"""
smelt_drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target[0]}, y={target[1]}),
    direction=Direction.DOWN,
)
smelt_drill = insert_item(Prototype.Coal, smelt_drill, quantity=20)
smelt_furnace = place_entity_next_to(
    Prototype.StoneFurnace,
    smelt_drill.position,
    direction=Direction.DOWN,
)
smelt_furnace = insert_item(Prototype.Coal, smelt_furnace, quantity=20)
sleep({settle_seconds})
print({{'furnace_inventory': inspect_inventory(smelt_furnace)}})
"""
    step = executor.execute(
        code,
        accept=validate_smelting,
        use_checkpoint_for_action=False,
    )

    if step.accepted:
        plates = measured["iron_plate_output"]
        journal.state["metrics"]["iron_plate_output"] = plates
        journal.complete_stage(
            3,
            f"Smelting cell accepted with {plates:.0f} iron plates produced.",
        )
        journal.event(
            "accept",
            "Direct drill-to-furnace smelting cell accepted.",
            iron_plate_output=plates,
        )
        lesson = synthesize_lesson(
            stage="smelting_probe",
            facts={
                "accepted": True,
                "iron_plate_output": plates,
                "engine_reward": step.reward,
            },
            fallback_lesson=(
                "A burner drill can directly feed a fueled stone furnace in "
                "this FLE scenario, producing measurable iron plates."
            ),
            fallback_hypothesis=(
                "Add output extraction and belt routing, then optimize the "
                "mining-to-smelting spatial layout with A*."
            ),
        )
        journal.event("knowledge", lesson["lesson"])
        return True

    journal.fail_stage(
        3,
        "Smelting probe produced no validated iron plates and was rolled back.",
    )
    journal.event(
        "reject",
        "Smelting probe rejected and checkpoint restored.",
        iron_plate_output=measured.get("iron_plate_output", 0.0),
    )
    lesson = synthesize_lesson(
        stage="smelting_probe",
        facts={
            "accepted": False,
            "iron_plate_output": measured.get("iron_plate_output", 0.0),
            "engine_reward": step.reward,
            "error": step.info.get("error"),
            "result": str(step.info.get("result"))[:1200],
        },
        fallback_lesson=(
            "The first direct smelting geometry did not yield validated iron "
            "plates, so transactional rollback preserved the scaled factory."
        ),
        fallback_hypothesis=(
            "Inspect furnace input geometry and test alternate relative "
            "placements before introducing belts."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return False


def run_curriculum(
    *,
    seed: int,
    placement_episodes: int,
    baseline_settle: int,
    trial_settle: int,
    scale_settle: int,
    smelt_settle: int,
    exploration: float,
) -> dict[str, Any]:
    import gym

    list_environments()
    env = gym.make("iron_ore_throughput", run_idx=0)
    executor = TransactionalFLEExecutor(env)
    run_id = datetime.now(UTC).strftime("curriculum-%Y%m%dT%H%M%SZ")
    journal = ResearchJournal(run_id)

    try:
        executor.reset(seed=seed)
        journal.event(
            "checkpoint",
            "Deterministic curriculum environment reset.",
            seed=seed,
        )

        _, center = stage_baseline(
            executor,
            env,
            journal,
            settle_seconds=baseline_settle,
        )
        best_arm = stage_online_learning(
            executor,
            env,
            journal,
            center=center,
            episodes=placement_episodes,
            settle_seconds=trial_settle,
            exploration=exploration,
        )
        stage_scale_mining(
            executor,
            env,
            journal,
            center=center,
            best_arm=best_arm,
            settle_seconds=scale_settle,
        )
        smelting_ok = stage_smelting_probe(
            executor,
            env,
            journal,
            center=center,
            settle_seconds=smelt_settle,
        )

        final_status = "completed" if smelting_ok else "partial_success"
        next_action = (
            "design belt/output extraction stage with A*"
            if smelting_ok
            else "run alternate smelting-geometry repair experiment"
        )
        journal.finish(final_status, next_action)
        return journal.state
    except Exception as exc:
        journal.state["status"] = "error"
        journal.state["detail"] = f"{type(exc).__name__}: {exc}"
        journal.event(
            "failure",
            "Curriculum runner stopped on an exception.",
            error=str(exc),
        )
        journal.finish(
            "error",
            "inspect counterexample and resume from last validated design",
        )
        raise
    finally:
        executor.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument("--placement-episodes", type=int, default=8)
    parser.add_argument("--baseline-settle", type=int, default=16)
    parser.add_argument("--trial-settle", type=int, default=8)
    parser.add_argument("--scale-settle", type=int, default=14)
    parser.add_argument("--smelt-settle", type=int, default=24)
    parser.add_argument("--exploration", type=float, default=2.0)
    args = parser.parse_args()

    result = run_curriculum(
        seed=args.seed,
        placement_episodes=args.placement_episodes,
        baseline_settle=args.baseline_settle,
        trial_settle=args.trial_settle,
        scale_settle=args.scale_settle,
        smelt_settle=args.smelt_settle,
        exploration=args.exploration,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
