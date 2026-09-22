from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from factorio_ai_lab.agents.llm_router import default_free_router
from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    fast_reposition,
    list_environments,
)
from factorio_ai_lab.learning.bandit import UCB1Bandit
from factorio_ai_lab.learning.evolution import challenger_genome
from factorio_ai_lab.learning.survival import (
    FitnessVector,
    compare_challenger,
    fitness_from_research,
)
from factorio_ai_lab.metrics.rates import normalized_rate_ratio, rate_per_second
from factorio_ai_lab.planning.astar import RoutingWeights, weighted_astar
from factorio_ai_lab.planning.progression import (
    DEFAULT_ENGINEERING_PLANNER,
    EngineeringState,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
RESEARCH_STATE = RUNS_DIR / "research_state.json"
KNOWLEDGE_LOG = RUNS_DIR / "knowledge.jsonl"
ACTIVE_RUN = RUNS_DIR / "active_run.json"
RESEARCH_HISTORY = RUNS_DIR / "research"
SPATIAL_DEMOS = RUNS_DIR / "datasets" / "spatial_demonstrations.jsonl"
EVOLUTION_CHAMPION = RUNS_DIR / "evolution_champion.json"
EVOLUTION_HISTORY = RUNS_DIR / "evolution_history.jsonl"

THROUGHPUT_EQUIVALENCE_TOLERANCE = 1.0

# Internal-coal allocation policy. Downstream consumers may spend only coal
# above the safety stock; the coal producer receives an operational refill so
# the supply chain remains productive while later stages execute.
COAL_SAFETY_STOCK = 4
COAL_PRODUCER_REFUEL = 3
COAL_COPPER_MINING_BUDGET = 2
COAL_COPPER_SMELTING_BUDGET = 2
COAL_SURVIVAL_BUDGET = 6


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


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def incumbent_champion() -> dict[str, Any]:
    return read_json_object(EVOLUTION_CHAMPION)


def patch_center(patch: Any) -> tuple[float, float]:
    box = patch.bounding_box
    return (
        (float(box.left_top.x) + float(box.right_bottom.x)) / 2.0,
        (float(box.left_top.y) + float(box.right_bottom.y)) / 2.0,
    )


class ResearchJournal:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        champion = incumbent_champion()
        champion_generation = int(champion.get("generation", 0) or 0)
        history_generation = 0
        if EVOLUTION_HISTORY.exists():
            try:
                for raw_line in EVOLUTION_HISTORY.read_text(
                    encoding="utf-8"
                ).splitlines():
                    if not raw_line.strip():
                        continue
                    row = json.loads(raw_line)
                    history_generation = max(
                        history_generation,
                        int(row.get("generation", 0) or 0),
                    )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                history_generation = 0
        generation = max(champion_generation, history_generation) + 1
        self.state: dict[str, Any] = {
            "run_id": run_id,
            "status": "starting",
            "arena": {
                "mode": "lab_play",
                "environment": "iron_ore_throughput",
                "inventory": "populated_benchmark",
                "technology": "pre_unlocked",
                "promotion_scope": "experimental_champion",
            },
            "objective": (
                "Evolve a self-sustaining production factory while preserving "
                "validated capabilities and promoting only surviving challengers"
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
                {
                    "name": "A* belt logistics",
                    "status": "pending",
                    "detail": (
                        "Route a real transport-belt line and validate ore delivery "
                        "through a burner inserter into a chest."
                    ),
                },
                {
                    "name": "Belt-fed smelting",
                    "status": "pending",
                    "detail": (
                        "Extend the promoted belt line into buffered smelting and "
                        "compare plate throughput against direct feed."
                    ),
                },
                {
                    "name": "Coal self-sufficiency",
                    "status": "pending",
                    "detail": (
                        "Discover coal, establish real coal extraction and retire "
                        "bootstrap fuel as an external dependency."
                    ),
                },
                {
                    "name": "Copper expansion",
                    "status": "pending",
                    "detail": (
                        "Discover a copper patch and establish validated copper mining."
                    ),
                },
                {
                    "name": "Copper smelting",
                    "status": "pending",
                    "detail": (
                        "Convert mined copper into validated copper-plate production."
                    ),
                },
                {
                    "name": "Capability survival soak",
                    "status": "pending",
                    "detail": (
                        "Refuel from internally mined coal and require iron, coal and "
                        "copper capabilities to remain alive simultaneously."
                    ),
                },
                {
                    "name": "Steam power",
                    "status": "pending",
                    "detail": (
                        "Build offshore pump, boiler and steam engine using endogenous "
                        "fuel and validate electric generation."
                    ),
                },
                {
                    "name": "Powered manufacturing",
                    "status": "pending",
                    "detail": (
                        "Use an electrically powered assembling machine to manufacture "
                        "iron gear wheels from the surviving iron chain."
                    ),
                },
                {
                    "name": "Automation science",
                    "status": "pending",
                    "detail": (
                        "Produce automation science packs in a powered assembler using "
                        "iron gears and copper plates."
                    ),
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
                    "weights": "static",
                    "learning_surface": "external typed knowledge memory",
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
            "engineering_progression": {
                "status": "bootstrapping",
                "achieved": [],
                "stalled_attempts": {},
                "frontier": [],
                "next_goal": None,
            },
            "resource_accounting": {
                "exogenous_inputs": {
                    "coal": {
                        "status": "bootstrap",
                        "reason": (
                            "Starter inventory is used only to bootstrap burner entities "
                            "until validated coal extraction exists."
                        ),
                    }
                }
            },
            "evolution": {
                "scheme": "incumbent_plus_challenger",
                "generation": generation,
                "retention_ratio": 0.80,
                "champion": champion or None,
                "challenger": {
                    "run_id": run_id,
                    "status": "evaluating",
                    "fitness": None,
                    "configuration": {},
                },
                "promotion": None,
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
        if status in {"completed", "generation_complete"}:
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
    journal.state["metrics"]["baseline_iron_rate_per_s"] = rate_per_second(
        measurement["iron_output"],
        settle_seconds,
    )
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
    champion = journal.state.get("evolution", {}).get("champion") or {}
    champion_config = (
        champion.get("configuration", {})
        if isinstance(champion, dict)
        else {}
    )
    incumbent_arm = champion_config.get("placement_best_arm")
    arm_order = list(PLACEMENT_ARMS)
    if incumbent_arm in PLACEMENT_ARMS:
        arm_order.remove(incumbent_arm)
        arm_order.insert(0, incumbent_arm)
    bandit = UCB1Bandit(tuple(arm_order), exploration=exploration)
    online = journal.state["online_learning"]
    online["incumbent_arm"] = (
        incumbent_arm if incumbent_arm in PLACEMENT_ARMS else None
    )
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
            "trial_reward_range": {
                "min": min(
                    float(row.get("reward", 0.0))
                    for row in online["history"]
                ),
                "max": max(
                    float(row.get("reward", 0.0))
                    for row in online["history"]
                ),
            },
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
    journal.state["metrics"]["scaled_iron_rate_per_s"] = rate_per_second(
        measured["iron_output"],
        settle_seconds,
    )
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
        plate_rate = rate_per_second(plates, float(settle_seconds))
        journal.state["metrics"].update(
            {
                "iron_plate_output": plates,
                "direct_smelting_duration_s": float(settle_seconds),
                "direct_smelting_plate_rate_per_s": plate_rate,
            }
        )
        journal.complete_stage(
            3,
            f"Smelting cell accepted with {plates:.0f} iron plates produced.",
        )
        journal.event(
            "accept",
            "Direct drill-to-furnace smelting cell accepted.",
            iron_plate_output=plates,
            duration_s=float(settle_seconds),
            plate_rate_per_s=plate_rate,
        )
        lesson = synthesize_lesson(
            stage="smelting_probe",
            facts={
                "accepted": True,
                "iron_plate_output": plates,
                "duration_s": float(settle_seconds),
                "plate_rate_per_s": plate_rate,
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



def _count_route_turns(path: tuple[GridPoint, ...]) -> int:
    turns = 0
    previous: tuple[int, int] | None = None
    for current, nxt in pairwise(path):
        direction = (nxt.x - current.x, nxt.y - current.y)
        if previous is not None and direction != previous:
            turns += 1
        previous = direction
    return turns


def _direction_name(current: GridPoint, nxt: GridPoint) -> str:
    delta = (nxt.x - current.x, nxt.y - current.y)
    names = {
        (1, 0): "RIGHT",
        (-1, 0): "LEFT",
        (0, 1): "DOWN",
        (0, -1): "UP",
    }
    try:
        return names[delta]
    except KeyError as exc:
        raise ValueError(f"non-cardinal belt segment: {delta}") from exc


def _chest_item_count(
    instance: Any,
    *,
    x: float,
    y: float,
    item: str,
) -> int:
    command = (
        "/c "
        "local p=storage.agent_characters and storage.agent_characters[1]; "
        "if not p then rcon.print('0') return end; "
        f"local e=p.surface.find_entity('wooden-chest',{{x={x},y={y}}}); "
        "if not e then rcon.print('0') return end; "
        "local inv=e.get_inventory(defines.inventory.chest); "
        f"rcon.print(inv and inv.get_item_count('{item}') or 0)"
    )
    raw = instance.rcon_client.send_command(command)
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def stage_astar_logistics(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    center: tuple[float, float],
    settle_seconds: int,
    turn_penalty: float,
) -> dict[str, Any] | None:
    namespace = env.unwrapped.instance.namespace
    instance = env.unwrapped.instance

    drill_position = (center[0] - 8.0, center[1] - 0.5)
    start_world = (drill_position[0] + 0.5, drill_position[1] + 1.5)
    goal_world = (drill_position[0] + 5.5, drill_position[1] + 4.5)
    start = GridPoint(round(start_world[0] - 0.5), round(start_world[1] - 0.5))
    goal = GridPoint(round(goal_world[0] - 0.5), round(goal_world[1] - 0.5))

    patch_bounds = journal.state.get("world", {}).get("patch_bounds", {})
    left_top = patch_bounds.get("left_top", {})
    right_bottom = patch_bounds.get("right_bottom", {})
    min_x = math.floor(float(left_top.get("x", center[0] - 14)))
    max_x = math.ceil(float(right_bottom.get("x", center[0] + 14)))
    min_y = math.floor(float(left_top.get("y", center[1] - 14)))
    max_y = math.ceil(float(right_bottom.get("y", center[1] + 14)))

    blocked: set[GridPoint] = set()
    entities = namespace._save_entity_state(
        distance=500,
        player_entities=True,
        resource_entities=False,
        items_on_ground=False,
        encode=False,
        compress=False,
    )
    large = {"burner-mining-drill", "stone-furnace"}
    for entity in entities:
        if entity.get("name") == "character":
            continue
        position = entity.get("position") or {}
        try:
            gx = round(float(position["x"]) - 0.5)
            gy = round(float(position["y"]) - 0.5)
        except (KeyError, TypeError, ValueError):
            continue
        radius = 1 if entity.get("name") in large else 0
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                blocked.add(GridPoint(gx + dx, gy + dy))

    blocked.discard(start)
    blocked.discard(goal)
    route = weighted_astar(
        start,
        goal,
        is_blocked=blocked.__contains__,
        in_bounds=lambda point: (
            min_x <= point.x <= max_x
            and min_y <= point.y <= max_y
        ),
        weights=RoutingWeights(
            step=1.0,
            turn=turn_penalty,
            occupied=8.0,
        ),
    )
    if route is None:
        journal.fail_stage(4, "A* could not find a valid logistics route.")
        return None

    path = route.path
    turns = _count_route_turns(path)
    belt_lines: list[str] = []
    for index, point in enumerate(path):
        if index + 1 < len(path):
            direction = _direction_name(point, path[index + 1])
        else:
            direction = "RIGHT"
        belt_lines.append(
            f"belt_{index}=place_entity("
            "Prototype.TransportBelt,"
            f"position=Position(x={point.x + 0.5},y={point.y + 0.5}),"
            f"direction=Direction.{direction}"
            ")"
        )

    last = path[-1]
    inserter_position = (last.x + 1.5, last.y + 0.5)
    chest_position = (last.x + 2.5, last.y + 0.5)

    journal.set_stage(
        4,
        status="validating",
        detail=(
            f"A* planned {len(path)} belts, {turns} turn(s), "
            f"cost {route.cost:.2f}; executing on the live world."
        ),
        next_action="validate ore reaches the logistics chest",
    )
    journal.event(
        "plan",
        "A* generated the first persistent belt route.",
        belt_count=len(path),
        turns=turns,
        cost=route.cost,
        expanded_nodes=route.expanded_nodes,
        path=[
            {"x": point.x + 0.5, "y": point.y + 0.5}
            for point in path
        ],
    )

    fast_reposition(
        env,
        x=drill_position[0],
        y=drill_position[1],
    )

    measured: dict[str, float] = {"chest_iron": 0.0}

    def validate_logistics(result: Any) -> bool:
        chest_iron = _chest_item_count(
            instance,
            x=chest_position[0],
            y=chest_position[1],
            item="iron-ore",
        )
        measured["chest_iron"] = float(chest_iron)
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and chest_iron > 0
        )

    code = f"""
logistics_drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={drill_position[0]},y={drill_position[1]}),
    direction=Direction.DOWN,
)
logistics_drill=insert_item(
    Prototype.Coal,
    logistics_drill,
    quantity=20,
)
{chr(10).join(belt_lines)}
logistics_inserter=place_entity(
    Prototype.BurnerInserter,
    position=Position(x={inserter_position[0]},y={inserter_position[1]}),
    direction=Direction.RIGHT,
)
logistics_inserter=insert_item(
    Prototype.Coal,
    logistics_inserter,
    quantity=10,
)
logistics_chest=place_entity(
    Prototype.WoodenChest,
    position=Position(x={chest_position[0]},y={chest_position[1]}),
    direction=Direction.UP,
)
sleep({settle_seconds})
print({{'logistics_chest': inspect_inventory(logistics_chest)}})
"""
    step = executor.execute(
        code,
        accept=validate_logistics,
        use_checkpoint_for_action=False,
    )

    if not step.accepted:
        journal.fail_stage(
            4,
            "Belt route failed to deliver ore; transaction rolled back.",
        )
        journal.event(
            "reject",
            "A* logistics route rejected and rolled back.",
            chest_iron=measured["chest_iron"],
            belt_count=len(path),
            turns=turns,
        )
        return None

    journal.state["metrics"].update(
        {
            "logistics_belt_count": len(path),
            "logistics_turns": turns,
            "logistics_route_cost": route.cost,
            "logistics_expanded_nodes": route.expanded_nodes,
            "logistics_chest_iron": measured["chest_iron"],
        }
    )
    journal.complete_stage(
        4,
        (
            f"A* logistics accepted: {len(path)} belts, {turns} turn(s), "
            f"{measured['chest_iron']:.0f} iron ore delivered to the final chest."
        ),
    )
    journal.event(
        "accept",
        "First persistent A* belt line accepted.",
        belt_count=len(path),
        turns=turns,
        cost=route.cost,
        expanded_nodes=route.expanded_nodes,
        chest_iron=measured["chest_iron"],
    )
    append_jsonl(
        SPATIAL_DEMOS,
        {
            "at": utc_now(),
            "run_id": journal.run_id,
            "task": "belt_route",
            "planner": "weighted_astar",
            "weights": asdict(
                RoutingWeights(
                    step=1.0,
                    turn=turn_penalty,
                    occupied=8.0,
                )
            ),
            "start": {
                "x": path[0].x + 0.5,
                "y": path[0].y + 0.5,
            },
            "goal": {
                "x": path[-1].x + 0.5,
                "y": path[-1].y + 0.5,
            },
            "path": [
                {"x": point.x + 0.5, "y": point.y + 0.5}
                for point in path
            ],
            "metrics": {
                "belt_count": len(path),
                "turns": turns,
                "route_cost": route.cost,
                "expanded_nodes": route.expanded_nodes,
                "delivered_iron": measured["chest_iron"],
            },
            "accepted": True,
        },
    )
    lesson = synthesize_lesson(
        stage="astar_belt_logistics",
        facts={
            "accepted": True,
            "belt_count": len(path),
            "turns": turns,
            "route_cost": route.cost,
            "expanded_nodes": route.expanded_nodes,
            "chest_iron": measured["chest_iron"],
            "route_start": {
                "x": path[0].x + 0.5,
                "y": path[0].y + 0.5,
            },
            "route_end": {
                "x": path[-1].x + 0.5,
                "y": path[-1].y + 0.5,
            },
        },
        fallback_lesson=(
            "The project A* planner generated a real belt route that moved "
            "iron ore through a fueled burner inserter into a terminal chest."
        ),
        fallback_hypothesis=(
            "Connect belt logistics to smelting output and compare throughput "
            "against direct-feed layouts using area and belt-count penalties."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return {
        "belt_count": len(path),
        "turns": turns,
        "route_cost": route.cost,
        "expanded_nodes": route.expanded_nodes,
        "chest_iron": measured["chest_iron"],
        "inserter_position": {
            "x": inserter_position[0],
            "y": inserter_position[1],
        },
        "chest_position": {
            "x": chest_position[0],
            "y": chest_position[1],
        },
        "path": [
            {"x": point.x + 0.5, "y": point.y + 0.5}
            for point in path
        ],
    }


def stage_belt_smelting(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    logistics: dict[str, Any],
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    chest = logistics["chest_position"]
    downstream_inserter = {
        "x": float(chest["x"]) + 1.0,
        "y": float(chest["y"]),
    }

    journal.set_stage(
        5,
        status="validating",
        detail=(
            "Extending the accepted A* route from its terminal buffer into "
            "a fueled stone furnace."
        ),
        next_action="measure belt-buffered iron plate throughput",
    )

    plate_before = production_output(namespace, "iron-plate")
    measured: dict[str, float] = {
        "iron_plate_before": plate_before,
        "iron_plate_output": 0.0,
    }

    def validate_belt_smelting(result: Any) -> bool:
        plate_after = production_output(namespace, "iron-plate")
        delta = max(0.0, plate_after - plate_before)
        measured["iron_plate_after"] = plate_after
        measured["iron_plate_output"] = delta
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and delta > 0
        )

    fast_reposition(
        env,
        x=downstream_inserter["x"],
        y=downstream_inserter["y"],
    )
    code = f"""
buffer_chest=get_entity(
    Prototype.WoodenChest,
    Position(x={chest["x"]},y={chest["y"]}),
)
smelt_out_inserter=place_entity(
    Prototype.BurnerInserter,
    position=Position(
        x={downstream_inserter["x"]},
        y={downstream_inserter["y"]}
    ),
    direction=Direction.RIGHT,
)
smelt_out_inserter=insert_item(
    Prototype.Coal,
    smelt_out_inserter,
    quantity=10,
)
belt_furnace=place_entity_next_to(
    Prototype.StoneFurnace,
    smelt_out_inserter.position,
    direction=Direction.RIGHT,
)
belt_furnace=insert_item(
    Prototype.Coal,
    belt_furnace,
    quantity=20,
)
sleep({settle_seconds})
print({{
    'buffer': inspect_inventory(buffer_chest),
    'furnace': inspect_inventory(belt_furnace),
}})
"""
    step = executor.execute(
        code,
        accept=validate_belt_smelting,
        use_checkpoint_for_action=False,
    )

    if not step.accepted:
        journal.fail_stage(
            5,
            "Buffered belt-to-furnace integration produced no validated plates; rolled back.",
        )
        journal.event(
            "reject",
            "Belt-fed smelting integration rejected and rolled back.",
            iron_plate_output=measured["iron_plate_output"],
        )
        return False

    plates = measured["iron_plate_output"]
    direct = float(journal.state["metrics"].get("iron_plate_output", 0.0))
    direct_duration = float(
        journal.state["metrics"].get("direct_smelting_duration_s", 0.0)
    )
    direct_rate = float(
        journal.state["metrics"].get(
            "direct_smelting_plate_rate_per_s",
            direct / direct_duration if direct_duration > 0 else 0.0,
        )
    )
    belt_count = max(1, int(logistics["belt_count"]))
    belt_rate = rate_per_second(plates, float(settle_seconds))
    ratio = normalized_rate_ratio(
        candidate_count=plates,
        candidate_duration_s=float(settle_seconds),
        baseline_count=direct,
        baseline_duration_s=direct_duration,
    )
    plate_rate_per_belt = belt_rate / belt_count

    journal.state["metrics"].update(
        {
            "belt_smelting_plate_output": plates,
            "belt_smelting_duration_s": float(settle_seconds),
            "belt_smelting_plate_rate_per_s": belt_rate,
            "belt_smelting_reward": step.reward,
            "belt_smelting_vs_direct_rate_ratio": ratio,
            "belt_smelting_plate_rate_per_belt_per_s": plate_rate_per_belt,
        }
    )
    journal.complete_stage(
        5,
        (
            f"Belt-fed smelting accepted with {plates:.0f} iron plates "
            f"over {settle_seconds}s ({belt_rate:.3f} plates/s); "
            f"{ratio:.3f}x the normalized direct-feed rate." if ratio is not None else "no valid direct-feed rate baseline."
        ),
    )
    journal.event(
        "accept",
        "Buffered belt-fed smelting accepted.",
        iron_plate_output=plates,
        direct_feed_output=direct,
        throughput_rate_ratio=ratio,
        belt_plate_rate_per_s=belt_rate,
        direct_plate_rate_per_s=direct_rate,
        plate_rate_per_belt_per_s=plate_rate_per_belt,
    )
    lesson = synthesize_lesson(
        stage="belt_fed_smelting",
        facts={
            "accepted": True,
            "belt_count": belt_count,
            "turns": logistics["turns"],
            "route_cost": logistics["route_cost"],
            "belt_smelting_plate_output": plates,
            "belt_smelting_duration_s": float(settle_seconds),
            "belt_smelting_plate_rate_per_s": belt_rate,
            "direct_feed_plate_output": direct,
            "direct_feed_duration_s": direct_duration,
            "direct_feed_plate_rate_per_s": direct_rate,
            "throughput_rate_ratio": ratio,
            "plate_rate_per_belt_per_s": plate_rate_per_belt,
        },
        fallback_lesson=(
            "The accepted A* belt corridor can feed a buffered furnace chain "
            "and produce validated iron plates."
        ),
        fallback_hypothesis=(
            "Run transactional route variants that jointly optimize plate "
            "throughput, belt count, turns and occupied tiles."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return True


def update_engineering_frontier(
    journal: ResearchJournal,
    *,
    achieved: set[str],
    stalled_attempts: dict[str, int] | None = None,
) -> dict[str, Any]:
    stalled = dict(stalled_attempts or {})
    state = EngineeringState(
        achieved=frozenset(achieved),
        stalled_attempts=stalled,
    )
    inferred = DEFAULT_ENGINEERING_PLANNER.inferred_achieved(state)
    ranked = DEFAULT_ENGINEERING_PLANNER.ranked_frontier(
        EngineeringState(
            achieved=inferred,
            stalled_attempts=stalled,
        )
    )
    frontier = [
        {
            "goal_id": candidate.goal.goal_id,
            "label": candidate.goal.label,
            "kind": candidate.goal.kind,
            "score": candidate.score,
            "novelty": candidate.novelty,
            "retry_penalty": candidate.retry_penalty,
        }
        for candidate in ranked
    ]
    progression = journal.state["engineering_progression"]
    progression["status"] = "active" if frontier else "frontier_complete"
    progression["achieved"] = sorted(inferred)
    progression["stalled_attempts"] = stalled
    progression["frontier"] = frontier
    progression["next_goal"] = frontier[0] if frontier else None
    journal.flush()
    return progression


def stage_coal_mining(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> tuple[bool, tuple[float, float] | None]:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        6,
        status="running",
        detail=(
            "Locating coal, quarantining remaining bootstrap fuel and requiring "
            "the mine to survive on coal that it produced itself."
        ),
        next_action="prove endogenous coal survival",
    )

    perception = executor.execute(
        """
coal = nearest(Resource.Coal)
coal_patch = get_resource_patch(Resource.Coal, coal, radius=30)
print({'coal': coal, 'patch': coal_patch})
""",
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
    )
    if not perception.accepted:
        journal.fail_stage(6, "Coal patch perception failed and was rolled back.")
        return False, None

    patch = namespace.coal_patch
    center = patch_center(patch)
    journal.state.setdefault("world", {})["coal_patch"] = {
        "size": patch.size,
        "center": {"x": center[0], "y": center[1]},
        "bounds": {
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
    journal.event(
        "world_model",
        "Coal resource patch added to the explicit world model.",
        center={"x": center[0], "y": center[1]},
        patch_size=patch.size,
    )

    fast_reposition(env, x=center[0], y=center[1])
    output_before = production_output(namespace, "coal")
    measured: dict[str, float] = {}
    # One external coal lasts roughly one burner-drill fuel cycle. We wait
    # beyond that cycle before transferring mined coal back into the drill, so
    # the second production window is causally powered by endogenous fuel.
    bootstrap_seed = 1
    seed_seconds = max(30, settle_seconds)

    def validate_coal(result: Any) -> bool:
        output_after = production_output(namespace, "coal")
        delta = max(0.0, output_after - output_before)
        measured["coal_output"] = delta
        for key in (
            "bootstrap_total",
            "bootstrap_quarantine",
            "seed_phase_count",
            "transfer_1",
            "internal_stock_before",
            "internal_stock_after",
            "endogenous_growth",
            "endogenous_stockpile",
            "operational_refuel",
        ):
            measured[key] = float(getattr(namespace, key, 0.0) or 0.0)
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and delta > 0
            and measured["transfer_1"] >= 1
            and measured["endogenous_growth"] > 0
            and measured["endogenous_stockpile"] > 0
        )

    code = f"""
bootstrap_total=inspect_inventory()[Prototype.Coal]
bootstrap_quarantine=max(0,bootstrap_total-{bootstrap_seed})
bootstrap_vault=place_entity(
    Prototype.WoodenChest,
    position=Position(x={center[0] + 5.5},y={center[1]}),
    exact=False,
)
if bootstrap_quarantine>0:
    bootstrap_vault=insert_item(
        Prototype.Coal,
        bootstrap_vault,
        quantity=bootstrap_quarantine,
    )

coal_drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={center[0]},y={center[1]}),
    direction=Direction.DOWN,
)
coal_drill=insert_item(
    Prototype.Coal,
    coal_drill,
    quantity={bootstrap_seed},
)
coal_chest=place_entity_next_to(
    Prototype.WoodenChest,
    coal_drill.position,
    direction=Direction.DOWN,
)

sleep({seed_seconds})
seed_phase_count=inspect_inventory(coal_chest)[Prototype.Coal]
transfer_1=0
if seed_phase_count>0:
    transfer_1=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(3,seed_phase_count),
    )

# The original one-coal seed has now had enough time to be exhausted. From
# here onward, any new output is powered by coal mined by this same cell.
if transfer_1>0:
    coal_drill=insert_item(
        Prototype.Coal,
        coal_drill,
        quantity=1,
    )
player_internal=inspect_inventory()[Prototype.Coal]
if player_internal>0:
    coal_chest=insert_item(
        Prototype.Coal,
        coal_chest,
        quantity=player_internal,
    )

internal_stock_before=inspect_inventory(coal_chest)[Prototype.Coal]
sleep({settle_seconds})
internal_stock_after=inspect_inventory(coal_chest)[Prototype.Coal]
endogenous_growth=max(0,internal_stock_after-internal_stock_before)

operational_refuel=0
available_for_refuel=max(
    0,
    internal_stock_after-{COAL_SAFETY_STOCK},
)
if available_for_refuel>0:
    operational_refuel=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min({COAL_PRODUCER_REFUEL},available_for_refuel),
    )
if operational_refuel>0:
    coal_drill=insert_item(
        Prototype.Coal,
        coal_drill,
        quantity=operational_refuel,
    )
endogenous_stockpile=inspect_inventory(coal_chest)[Prototype.Coal]

print({{
    'bootstrap_total':bootstrap_total,
    'bootstrap_quarantine':bootstrap_quarantine,
    'seed_phase_count':seed_phase_count,
    'transfer_1':transfer_1,
    'internal_stock_before':internal_stock_before,
    'internal_stock_after':internal_stock_after,
    'endogenous_growth':endogenous_growth,
    'endogenous_stockpile':endogenous_stockpile,
    'operational_refuel':operational_refuel,
    'player_coal_after':inspect_inventory()[Prototype.Coal],
}})
"""
    step = executor.execute(
        code,
        accept=validate_coal,
        use_checkpoint_for_action=False,
    )
    if not step.accepted:
        journal.fail_stage(
            6,
            (
                "Coal failed endogenous-fuel survival: bootstrap fuel was "
                "quarantined and the mine did not survive a second window."
            ),
        )
        journal.event(
            "reject",
            "Coal self-sufficiency challenger rejected.",
            measurements=measured,
        )
        return False, center

    output = measured["coal_output"]
    coal_chest = namespace.coal_chest
    bootstrap_vault = namespace.bootstrap_vault
    duration = float(seed_seconds + settle_seconds)
    journal.state["metrics"].update(
        {
            "coal_output": output,
            "coal_mining_duration_s": duration,
            "coal_rate_per_s": rate_per_second(output, duration),
            "coal_bootstrap_seed": float(bootstrap_seed),
            "coal_bootstrap_quarantined": measured["bootstrap_quarantine"],
            "coal_endogenous_transfer": measured["transfer_1"],
            "coal_endogenous_growth": measured["endogenous_growth"],
            "coal_endogenous_stockpile": measured["endogenous_stockpile"],
            "coal_operational_refuel": measured["operational_refuel"],
            "coal_safety_stock_target": float(COAL_SAFETY_STOCK),
            "coal_mining_reward": step.reward,
        }
    )
    coal_world = journal.state.setdefault("world", {}).setdefault("coal_patch", {})
    coal_world["output_chest"] = {
        "x": float(coal_chest.position.x),
        "y": float(coal_chest.position.y),
    }
    coal_world["bootstrap_vault"] = {
        "x": float(bootstrap_vault.position.x),
        "y": float(bootstrap_vault.position.y),
    }

    accounting = journal.state["resource_accounting"]["exogenous_inputs"]["coal"]
    accounting.update(
        {
            "status": "self_sufficient",
            "validated_internal_production": output,
            "bootstrap_seed_used": bootstrap_seed,
            "bootstrap_quarantined": measured["bootstrap_quarantine"],
            "endogenous_transfer": measured["transfer_1"],
            "endogenous_growth": measured["endogenous_growth"],
            "endogenous_stockpile": measured["endogenous_stockpile"],
            "operational_refuel": measured["operational_refuel"],
            "safety_stock_target": COAL_SAFETY_STOCK,
        }
    )

    journal.complete_stage(
        6,
        (
            f"Coal survived endogenous refueling: {output:.0f} produced, "
            f"{measured['endogenous_stockpile']:.0f} buffered internally, with "
            f"{measured['bootstrap_quarantine']:.0f} bootstrap coal quarantined."
        ),
    )
    journal.event(
        "accept",
        "Coal extraction survived a second window using internally mined fuel.",
        coal_output=output,
        bootstrap_seed=bootstrap_seed,
        bootstrap_quarantined=measured["bootstrap_quarantine"],
        endogenous_transfer=measured["transfer_1"],
        endogenous_growth=measured["endogenous_growth"],
        center={"x": center[0], "y": center[1]},
    )
    lesson = synthesize_lesson(
        stage="coal_self_sufficiency",
        facts={
            "accepted": True,
            "coal_output": output,
            "bootstrap_seed": bootstrap_seed,
            "bootstrap_quarantined": measured["bootstrap_quarantine"],
            "endogenous_transfer": measured["transfer_1"],
            "endogenous_growth": measured["endogenous_growth"],
            "sustained_second_window": measured["internal_stock_after"],
            "patch_size": patch.size,
        },
        fallback_lesson=(
            "Coal is self-sustaining only after bootstrap fuel is quarantined "
            "and mined coal keeps the drill alive through a second window."
        ),
        fallback_hypothesis=(
            "Use the endogenous coal buffer to keep iron and copper capabilities "
            "alive simultaneously before promoting the factory."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return True, center


def stage_copper_mining(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> tuple[bool, tuple[float, float] | None]:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        7,
        status="running",
        detail="Expanding the factory to a measured copper resource patch.",
        next_action="discover and validate copper extraction",
    )

    perception = executor.execute(
        """
copper = nearest(Resource.CopperOre)
copper_patch = get_resource_patch(Resource.CopperOre, copper, radius=30)
print({'copper': copper, 'patch': copper_patch})
""",
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
    )
    if not perception.accepted:
        journal.fail_stage(7, "Copper patch perception failed and was rolled back.")
        return False, None

    patch = namespace.copper_patch
    center = patch_center(patch)
    journal.state.setdefault("world", {})["copper_patch"] = {
        "size": patch.size,
        "center": {"x": center[0], "y": center[1]},
        "bounds": {
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
    journal.event(
        "world_model",
        "Copper resource patch added to the explicit world model.",
        center={"x": center[0], "y": center[1]},
        patch_size=patch.size,
    )

    fast_reposition(env, x=center[0], y=center[1])
    output_before = production_output(namespace, "copper-ore")
    measured: dict[str, float] = {}

    def validate_copper(result: Any) -> bool:
        output_after = production_output(namespace, "copper-ore")
        delta = max(0.0, output_after - output_before)
        measured["copper_ore_output"] = delta
        measured["internal_fuel"] = float(
            getattr(namespace, "copper_mining_fuel", 0.0) or 0.0
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and measured["internal_fuel"] > 0
            and delta > 0
        )

    code = f"""
move_to(coal_chest.position)
copper_mining_fuel=0
for _ in range(6):
    available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
    spendable=max(0,available_internal-{COAL_SAFETY_STOCK})
    if spendable>0:
        break
    sleep(6)
available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
spendable=max(0,available_internal-{COAL_SAFETY_STOCK})
if spendable>0:
    copper_mining_fuel=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min({COAL_COPPER_MINING_BUDGET},spendable),
    )

move_to(Position(x={center[0]},y={center[1]}))
copper_drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={center[0]},y={center[1]}),
    direction=Direction.DOWN,
)
if copper_mining_fuel>0:
    copper_drill=insert_item(
        Prototype.Coal,
        copper_drill,
        quantity=copper_mining_fuel,
    )
copper_chest=place_entity_next_to(
    Prototype.WoodenChest,
    copper_drill.position,
    direction=Direction.DOWN,
)
sleep({settle_seconds})
print({{'copper_inventory': inspect_inventory(copper_chest)}})
"""
    step = executor.execute(
        code,
        accept=validate_copper,
        use_checkpoint_for_action=False,
    )
    if not step.accepted:
        journal.fail_stage(
            7,
            "Copper mining produced no validated output; transaction rolled back.",
        )
        journal.event("reject", "Copper expansion rejected and rolled back.")
        return False, center

    output = measured["copper_ore_output"]
    journal.state["metrics"]["copper_ore_output"] = output
    journal.state["metrics"]["copper_mining_duration_s"] = float(settle_seconds)
    journal.state["metrics"]["copper_ore_rate_per_s"] = rate_per_second(
        output,
        settle_seconds,
    )
    journal.state["metrics"]["copper_mining_reward"] = step.reward
    journal.state["metrics"]["copper_mining_internal_coal"] = measured.get(
        "internal_fuel",
        0.0,
    )
    journal.complete_stage(
        7,
        f"Copper mining accepted with {output:.0f} copper ore produced.",
    )
    journal.event(
        "accept",
        "First persistent copper mining cell accepted.",
        copper_ore_output=output,
        center={"x": center[0], "y": center[1]},
    )
    lesson = synthesize_lesson(
        stage="copper_mining",
        facts={
            "accepted": True,
            "copper_ore_output": output,
            "patch_size": patch.size,
        },
        fallback_lesson=(
            "The factory diversified beyond iron by establishing validated "
            "copper extraction on a measured live resource patch."
        ),
        fallback_hypothesis=(
            "Smelt copper ore into copper plates, then open the electronics "
            "and science dependency chain."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return True, center


def stage_copper_smelting(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    center: tuple[float, float],
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace

    journal.set_stage(
        8,
        status="validating",
        detail=(
            "Feeding a buffered copper furnace from the already validated "
            "copper mine, using only endogenous coal."
        ),
        next_action="validate buffered copper plate production",
    )
    plate_before = production_output(namespace, "copper-plate")
    measured: dict[str, float] = {}

    def validate_smelting(result: Any) -> bool:
        plate_after = production_output(namespace, "copper-plate")
        delta = max(0.0, plate_after - plate_before)
        measured["copper_plate_output"] = delta
        for key in (
            "copper_ore_transfer",
            "copper_smelting_fuel",
            "copper_furnace_inventory",
        ):
            measured[key] = float(getattr(namespace, key, 0.0) or 0.0)
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and measured["copper_ore_transfer"] > 0
            and measured["copper_smelting_fuel"] > 0
            and delta > 0
        )

    code = f"""
move_to(copper_chest.position)
copper_ore_available=inspect_inventory(copper_chest)[Prototype.CopperOre]
copper_ore_transfer=0
if copper_ore_available>0:
    copper_ore_transfer=extract_item(
        Prototype.CopperOre,
        copper_chest,
        quantity=min(32,copper_ore_available),
    )

move_to(coal_chest.position)
copper_smelting_fuel=0
for _ in range(6):
    available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
    spendable=max(0,available_internal-{COAL_SAFETY_STOCK})
    if spendable>0:
        break
    sleep(6)
available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
spendable=max(0,available_internal-{COAL_SAFETY_STOCK})
if spendable>0:
    copper_smelting_fuel=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min({COAL_COPPER_SMELTING_BUDGET},spendable),
    )

furnace_box=BuildingBox(
    width=Prototype.StoneFurnace.WIDTH+6,
    height=Prototype.StoneFurnace.HEIGHT+6,
)
furnace_area=nearest_buildable(
    Prototype.StoneFurnace,
    furnace_box,
    copper_chest.position,
)
move_to(furnace_area.center)
copper_furnace=place_entity(
    Prototype.StoneFurnace,
    position=furnace_area.center,
)
if copper_smelting_fuel>0:
    copper_furnace=insert_item(
        Prototype.Coal,
        copper_furnace,
        quantity=copper_smelting_fuel,
    )
if copper_ore_transfer>0:
    copper_furnace=insert_item(
        Prototype.CopperOre,
        copper_furnace,
        quantity=copper_ore_transfer,
    )

sleep({settle_seconds})
copper_furnace_inventory=inspect_inventory(
    copper_furnace,
)[Prototype.CopperPlate]
print({{
    'copper_ore_transfer':copper_ore_transfer,
    'copper_smelting_fuel':copper_smelting_fuel,
    'copper_furnace_inventory':copper_furnace_inventory,
}})
"""
    step = executor.execute(
        code,
        accept=validate_smelting,
        use_checkpoint_for_action=False,
    )
    if not step.accepted:
        journal.fail_stage(
            8,
            (
                "Buffered copper smelting produced no validated copper plates; "
                "transaction rolled back."
            ),
        )
        journal.event(
            "reject",
            "Copper smelting rejected and rolled back.",
            measurements=measured,
        )
        return False

    plates = measured["copper_plate_output"]
    journal.state["metrics"]["copper_plate_output"] = plates
    journal.state["metrics"]["copper_smelting_duration_s"] = float(settle_seconds)
    journal.state["metrics"]["copper_plate_rate_per_s"] = rate_per_second(
        plates,
        settle_seconds,
    )
    journal.state["metrics"]["copper_smelting_reward"] = step.reward
    journal.state["metrics"]["copper_smelting_internal_coal"] = measured.get(
        "copper_smelting_fuel",
        0.0,
    )
    journal.state["metrics"]["copper_smelting_buffered_ore"] = measured.get(
        "copper_ore_transfer",
        0.0,
    )
    journal.complete_stage(
        8,
        f"Buffered copper smelting accepted with {plates:.0f} copper plates.",
    )
    journal.event(
        "accept",
        "Buffered copper furnace accepted using endogenous coal.",
        copper_plate_output=plates,
        copper_ore_transfer=measured.get("copper_ore_transfer", 0.0),
        endogenous_coal=measured.get("copper_smelting_fuel", 0.0),
    )
    lesson = synthesize_lesson(
        stage="copper_smelting",
        facts={
            "accepted": True,
            "copper_plate_output": plates,
            "buffered_copper_ore": measured.get("copper_ore_transfer", 0.0),
            "endogenous_coal": measured.get("copper_smelting_fuel", 0.0),
            "engine_reward": step.reward,
        },
        fallback_lesson=(
            "Copper smelting is more robust when the validated mine feeds a "
            "buffer and a separate furnace consumes that buffer with endogenous coal."
        ),
        fallback_hypothesis=(
            "Keep iron, coal and copper productive simultaneously, then promote "
            "steam power only if the survival soak passes."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return True


def stage_capability_survival(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    iron_center: tuple[float, float],
    coal_center: tuple[float, float],
    copper_center: tuple[float, float],
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        9,
        status="validating",
        detail=(
            "Allocate endogenous coal under a safety-stock policy and require "
            "iron, coal, copper mining and copper smelting to survive together."
        ),
        next_action="run simultaneous capability survival soak",
    )

    before = {
        "iron_ore": production_output(namespace, "iron-ore"),
        "iron_plate": production_output(namespace, "iron-plate"),
        "coal": production_output(namespace, "coal"),
        "copper_ore": production_output(namespace, "copper-ore"),
        "copper_plate": production_output(namespace, "copper-plate"),
    }
    measured: dict[str, float] = {}

    code = f"""
move_to(coal_chest.position)
survival_transfer=0
survival_wait_seconds=0
for _ in range(8):
    available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
    if available_internal>={COAL_SURVIVAL_BUDGET}:
        break
    sleep(6)
    survival_wait_seconds+=6
available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
if available_internal>0:
    survival_transfer=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min({COAL_SURVIVAL_BUDGET},available_internal),
    )

refueled_count=0
for entity in (
    drill,
    scale_drill,
    coal_drill,
    copper_drill,
    copper_furnace,
):
    if inspect_inventory()[Prototype.Coal]>=1:
        entity=insert_item(
            Prototype.Coal,
            entity,
            quantity=1,
        )
        refueled_count+=1

sleep(8)
survival_copper_feed=0
move_to(copper_chest.position)
fresh_copper=inspect_inventory(copper_chest)[Prototype.CopperOre]
if fresh_copper>0:
    survival_copper_feed=extract_item(
        Prototype.CopperOre,
        copper_chest,
        quantity=min(8,fresh_copper),
    )
if survival_copper_feed>0:
    move_to(copper_furnace.position)
    copper_furnace=insert_item(
        Prototype.CopperOre,
        copper_furnace,
        quantity=survival_copper_feed,
    )

sleep({settle_seconds})
survival_coal_reserve=inspect_inventory(coal_chest)[Prototype.Coal]
print({{
    'survival_transfer':survival_transfer,
    'survival_wait_seconds':survival_wait_seconds,
    'refueled_count':refueled_count,
    'survival_copper_feed':survival_copper_feed,
    'survival_coal_reserve':survival_coal_reserve,
}})
"""

    def validate(result: Any) -> bool:
        after = {
            "iron_ore": production_output(namespace, "iron-ore"),
            "iron_plate": production_output(namespace, "iron-plate"),
            "coal": production_output(namespace, "coal"),
            "copper_ore": production_output(namespace, "copper-ore"),
            "copper_plate": production_output(namespace, "copper-plate"),
        }
        measured["iron"] = max(
            0.0,
            after["iron_ore"] - before["iron_ore"],
        ) + max(0.0, after["iron_plate"] - before["iron_plate"])
        measured["coal"] = max(0.0, after["coal"] - before["coal"])
        measured["copper_ore"] = max(
            0.0,
            after["copper_ore"] - before["copper_ore"],
        )
        measured["copper_plate"] = max(
            0.0,
            after["copper_plate"] - before["copper_plate"],
        )
        for key in (
            "refueled_count",
            "survival_transfer",
            "survival_wait_seconds",
            "survival_copper_feed",
            "survival_coal_reserve",
        ):
            measured[key] = float(getattr(namespace, key, 0.0) or 0.0)
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and measured["survival_transfer"] >= 5
            and measured["refueled_count"] >= 5
            and measured["survival_copper_feed"] > 0
            and measured["iron"] > 0
            and measured["coal"] > 0
            and measured["copper_ore"] > 0
            and measured["copper_plate"] > 0
        )

    step = executor.execute(
        code,
        accept=validate,
        use_checkpoint_for_action=False,
    )
    total_window = settle_seconds + 8
    journal.state["metrics"].update(
        {
            "survival_iron_output": measured.get("iron", 0.0),
            "survival_coal_output": measured.get("coal", 0.0),
            "survival_copper_output": (
                measured.get("copper_ore", 0.0)
                + measured.get("copper_plate", 0.0)
            ),
            "survival_copper_ore_output": measured.get("copper_ore", 0.0),
            "survival_copper_plate_output": measured.get("copper_plate", 0.0),
            "survival_iron_rate_per_s": rate_per_second(
                measured.get("iron", 0.0),
                total_window,
            ),
            "survival_coal_rate_per_s": rate_per_second(
                measured.get("coal", 0.0),
                total_window,
            ),
            "survival_copper_rate_per_s": rate_per_second(
                measured.get("copper_ore", 0.0)
                + measured.get("copper_plate", 0.0),
                total_window,
            ),
            "survival_refueled_entities": measured.get("refueled_count", 0.0),
            "survival_internal_coal_transfer": measured.get(
                "survival_transfer",
                0.0,
            ),
            "survival_coal_reserve": measured.get(
                "survival_coal_reserve",
                0.0,
            ),
            "survival_wait_seconds": measured.get(
                "survival_wait_seconds",
                0.0,
            ),
        }
    )
    if not step.accepted:
        journal.fail_stage(
            9,
            (
                "Survival gate failed: iron, endogenous coal, copper mining and "
                "copper smelting did not all remain productive together."
            ),
        )
        journal.event(
            "selection",
            "Challenger failed capability-retention survival gate.",
            measurements=measured,
        )
        return False

    journal.complete_stage(
        9,
        (
            "Survival gate passed: iron, coal, copper mining and copper "
            "smelting remained productive simultaneously."
        ),
    )
    journal.event(
        "selection",
        "Challenger passed simultaneous capability-retention gate.",
        measurements=measured,
    )
    return True


def stage_steam_power(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        10,
        status="running",
        detail=(
            "Build a real offshore-pump, boiler and steam-engine chain and "
            "validate generated electrical energy."
        ),
        next_action="establish steam power",
    )

    measured: dict[str, float] = {}
    code = f"""
move_to(coal_chest.position)
coal_for_power=0
available=inspect_inventory(coal_chest)[Prototype.Coal]
if available>0:
    coal_for_power=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(12,available),
    )

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
if inspect_inventory()[Prototype.Coal]>0:
    boiler=insert_item(
        Prototype.Coal,
        boiler,
        quantity=min(8,inspect_inventory()[Prototype.Coal]),
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
water_pipes=connect_entities(
    offshore_pump,
    boiler,
    Prototype.Pipe,
)
steam_pipes=connect_entities(
    boiler,
    steam_engine,
    Prototype.Pipe,
)
sleep({settle_seconds})
steam_engine=get_entity(
    Prototype.SteamEngine,
    steam_engine.position,
)
steam_energy=float(steam_engine.energy or 0)
print({{
    'steam_energy':steam_energy,
    'coal_for_power':coal_for_power,
}})
"""

    def validate(result: Any) -> bool:
        measured["energy"] = float(
            getattr(namespace, "steam_energy", 0.0) or 0.0
        )
        measured["coal"] = float(
            getattr(namespace, "coal_for_power", 0.0) or 0.0
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and measured["coal"] > 0
            and measured["energy"] > 0
        )

    step = executor.execute(
        code,
        accept=validate,
        use_checkpoint_for_action=False,
    )
    journal.state["metrics"]["steam_energy"] = measured.get("energy", 0.0)
    journal.state["metrics"]["steam_power_internal_coal"] = measured.get(
        "coal",
        0.0,
    )
    if not step.accepted:
        journal.fail_stage(
            10,
            "Steam-power chain produced no validated electrical energy.",
        )
        journal.event(
            "reject",
            "Steam-power challenger rejected.",
            measurements=measured,
        )
        return False

    journal.complete_stage(
        10,
        f"Steam power accepted with {measured['energy']:.0f} J stored energy.",
    )
    journal.event(
        "accept",
        "Offshore pump, boiler and steam engine formed a working power system.",
        measurements=measured,
    )
    return True


def stage_powered_manufacturing(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        11,
        status="running",
        detail=(
            "Feed surviving iron plates into an electrically powered assembler "
            "and validate iron-gear-wheel production."
        ),
        next_action="manufacture iron gears electrically",
    )
    before = production_output(namespace, "iron-gear-wheel")
    measured: dict[str, float] = {}

    code = f"""
move_to(belt_furnace.position)
iron_plate_transfer=0
belt_plate_count=inspect_inventory(belt_furnace)[Prototype.IronPlate]
if belt_plate_count>0:
    iron_plate_transfer=extract_item(
        Prototype.IronPlate,
        belt_furnace,
        quantity=min(48,belt_plate_count),
    )
if iron_plate_transfer<8:
    move_to(smelt_furnace.position)
    direct_plate_count=inspect_inventory(smelt_furnace)[Prototype.IronPlate]
    if direct_plate_count>0:
        iron_plate_transfer+=extract_item(
            Prototype.IronPlate,
            smelt_furnace,
            quantity=min(48,direct_plate_count),
        )

assembler_box=BuildingBox(
    width=Prototype.AssemblingMachine2.WIDTH+6,
    height=Prototype.AssemblingMachine2.HEIGHT+6,
)
assembler_area=nearest_buildable(
    Prototype.AssemblingMachine2,
    assembler_box,
    steam_engine.position,
)
move_to(assembler_area.center)
gear_assembler=place_entity(
    Prototype.AssemblingMachine2,
    position=assembler_area.center,
)
gear_assembler=set_entity_recipe(
    gear_assembler,
    Prototype.IronGearWheel,
)
if iron_plate_transfer>0:
    gear_assembler=insert_item(
        Prototype.IronPlate,
        gear_assembler,
        quantity=iron_plate_transfer,
    )
gear_power=connect_entities(
    steam_engine,
    gear_assembler,
    Prototype.MediumElectricPole,
)
sleep({settle_seconds})
gear_inventory=inspect_inventory(gear_assembler)[Prototype.IronGearWheel]
print({{
    'iron_plate_transfer':iron_plate_transfer,
    'gear_inventory':gear_inventory,
}})
"""

    def validate(result: Any) -> bool:
        delta = max(
            0.0,
            production_output(namespace, "iron-gear-wheel") - before,
        )
        measured["output"] = delta
        measured["inventory"] = float(
            getattr(namespace, "gear_inventory", 0.0) or 0.0
        )
        measured["plates"] = float(
            getattr(namespace, "iron_plate_transfer", 0.0) or 0.0
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and measured["plates"] > 0
            and (delta > 0 or measured["inventory"] > 0)
        )

    step = executor.execute(
        code,
        accept=validate,
        use_checkpoint_for_action=False,
    )
    output = max(measured.get("output", 0.0), measured.get("inventory", 0.0))
    journal.state["metrics"]["iron_gear_wheel_output"] = output
    journal.state["metrics"]["iron_gear_wheel_rate_per_s"] = rate_per_second(
        output,
        settle_seconds,
    )
    if not step.accepted:
        journal.fail_stage(
            11,
            "Powered assembler produced no validated iron gear wheels.",
        )
        journal.event(
            "reject",
            "Powered-manufacturing challenger rejected.",
            measurements=measured,
        )
        return False

    journal.complete_stage(
        11,
        f"Powered manufacturing accepted with {output:.0f} iron gears.",
    )
    journal.event(
        "accept",
        "Electric assembler manufactured iron gear wheels.",
        measurements=measured,
    )
    return True


def stage_automation_science(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        12,
        status="running",
        detail=(
            "Combine electrically manufactured iron gears with copper plates "
            "in a powered assembler to produce automation science."
        ),
        next_action="produce automation science packs",
    )
    before = production_output(namespace, "automation-science-pack")
    measured: dict[str, float] = {}

    code = f"""
move_to(gear_assembler.position)
gear_transfer=0
gear_count=inspect_inventory(gear_assembler)[Prototype.IronGearWheel]
if gear_count>0:
    gear_transfer=extract_item(
        Prototype.IronGearWheel,
        gear_assembler,
        quantity=min(32,gear_count),
    )
move_to(copper_furnace.position)
copper_plate_transfer=0
copper_plate_count=inspect_inventory(copper_furnace)[Prototype.CopperPlate]
if copper_plate_count>0:
    copper_plate_transfer=extract_item(
        Prototype.CopperPlate,
        copper_furnace,
        quantity=min(32,copper_plate_count),
    )

science_box=BuildingBox(
    width=Prototype.AssemblingMachine2.WIDTH+6,
    height=Prototype.AssemblingMachine2.HEIGHT+6,
)
science_area=nearest_buildable(
    Prototype.AssemblingMachine2,
    science_box,
    gear_assembler.position,
)
move_to(science_area.center)
science_assembler=place_entity(
    Prototype.AssemblingMachine2,
    position=science_area.center,
)
science_assembler=set_entity_recipe(
    science_assembler,
    Prototype.AutomationSciencePack,
)
if gear_transfer>0:
    science_assembler=insert_item(
        Prototype.IronGearWheel,
        science_assembler,
        quantity=gear_transfer,
    )
if copper_plate_transfer>0:
    science_assembler=insert_item(
        Prototype.CopperPlate,
        science_assembler,
        quantity=copper_plate_transfer,
    )
science_power=connect_entities(
    steam_engine,
    science_assembler,
    Prototype.MediumElectricPole,
)
sleep({settle_seconds})
science_inventory=inspect_inventory(
    science_assembler,
)[Prototype.AutomationSciencePack]
print({{
    'gear_transfer':gear_transfer,
    'copper_plate_transfer':copper_plate_transfer,
    'science_inventory':science_inventory,
}})
"""

    def validate(result: Any) -> bool:
        delta = max(
            0.0,
            production_output(namespace, "automation-science-pack") - before,
        )
        measured["output"] = delta
        measured["inventory"] = float(
            getattr(namespace, "science_inventory", 0.0) or 0.0
        )
        measured["gears"] = float(
            getattr(namespace, "gear_transfer", 0.0) or 0.0
        )
        measured["copper"] = float(
            getattr(namespace, "copper_plate_transfer", 0.0) or 0.0
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and measured["gears"] > 0
            and measured["copper"] > 0
            and (delta > 0 or measured["inventory"] > 0)
        )

    step = executor.execute(
        code,
        accept=validate,
        use_checkpoint_for_action=False,
    )
    output = max(measured.get("output", 0.0), measured.get("inventory", 0.0))
    journal.state["metrics"]["automation_science_output"] = output
    journal.state["metrics"]["automation_science_rate_per_s"] = rate_per_second(
        output,
        settle_seconds,
    )
    if not step.accepted:
        journal.fail_stage(
            12,
            "Powered assembler produced no validated automation science packs.",
        )
        journal.event(
            "reject",
            "Automation-science challenger rejected.",
            measurements=measured,
        )
        return False

    journal.complete_stage(
        12,
        f"Automation science accepted with {output:.0f} packs produced.",
    )
    journal.event(
        "accept",
        "Powered factory produced automation science packs.",
        measurements=measured,
    )
    lesson = synthesize_lesson(
        stage="automation_science",
        facts={
            "accepted": True,
            "automation_science_output": output,
            "iron_gears": measured.get("gears", 0.0),
            "copper_plates": measured.get("copper", 0.0),
        },
        fallback_lesson=(
            "The factory crossed from resource extraction into powered "
            "manufacturing and automation-science production."
        ),
        fallback_hypothesis=(
            "Move to an open-play tech-tree environment, feed a real lab and "
            "research Automation before scaling green science."
        ),
    )
    journal.event("knowledge", lesson["lesson"])
    return True


def finalize_evolution_selection(
    journal: ResearchJournal,
    *,
    achieved: set[str],
) -> dict[str, Any]:
    failed_stages = sum(
        1
        for stage in journal.state.get("curriculum", [])
        if isinstance(stage, dict) and stage.get("status") == "failed"
    )
    challenger = fitness_from_research(
        metrics=journal.state.get("metrics", {}),
        achieved=achieved,
        resource_accounting=journal.state.get("resource_accounting", {}),
        failed_stages=failed_stages,
    )

    incumbent = incumbent_champion()
    incumbent_fitness: FitnessVector | None = None
    if isinstance(incumbent.get("fitness"), dict):
        incumbent_fitness = FitnessVector.from_dict(incumbent["fitness"])

    evolution = journal.state.setdefault("evolution", {})
    retention_ratio = float(evolution.get("retention_ratio", 0.80) or 0.80)
    decision = compare_challenger(
        incumbent_fitness,
        challenger,
        retention_ratio=retention_ratio,
    )

    metrics = journal.state.get("metrics", {})
    configuration = {
        "placement_best_arm": metrics.get("placement_best_arm"),
        "placement_ucb_best_arm": metrics.get("placement_ucb_best_arm"),
        "routing_turn_penalty": evolution.get("challenger", {})
        .get("configuration", {})
        .get("routing_turn_penalty"),
        "placement_exploration": evolution.get("challenger", {})
        .get("configuration", {})
        .get("placement_exploration"),
    }
    generation = int(evolution.get("generation", 1) or 1)
    candidate_record = {
        "run_id": journal.run_id,
        "generation": generation,
        "selected_at": utc_now(),
        "fitness": challenger.to_dict(),
        "configuration": configuration,
        "knowledge_count_at_selection": sum(
            1
            for _ in KNOWLEDGE_LOG.open(encoding="utf-8")
        )
        if KNOWLEDGE_LOG.exists()
        else 0,
    }

    challenger_state = evolution.setdefault("challenger", {})
    challenger_state.update(candidate_record)
    challenger_state["status"] = (
        "promoted" if decision.promoted else "rejected"
    )
    evolution["promotion"] = decision.to_dict()

    if decision.promoted:
        atomic_json(EVOLUTION_CHAMPION, candidate_record)
        evolution["champion"] = candidate_record
    else:
        evolution["champion"] = incumbent or None

    append_jsonl(
        EVOLUTION_HISTORY,
        {
            "at": utc_now(),
            "generation": generation,
            "challenger": candidate_record,
            "incumbent_run_id": incumbent.get("run_id") if incumbent else None,
            "decision": decision.to_dict(),
        },
    )
    journal.event(
        "selection",
        (
            "Challenger promoted to champion."
            if decision.promoted
            else "Incumbent champion retained."
        ),
        generation=generation,
        decision=decision.to_dict(),
        fitness=challenger.to_dict(),
    )
    return decision.to_dict()


def run_curriculum(
    *,
    seed: int,
    placement_episodes: int,
    baseline_settle: int,
    trial_settle: int,
    scale_settle: int,
    smelt_settle: int,
    logistics_settle: int,
    belt_smelt_settle: int,
    coal_mine_settle: int,
    copper_mine_settle: int,
    copper_smelt_settle: int,
    exploration: float,
) -> dict[str, Any]:
    import gym

    list_environments()
    env = gym.make("iron_ore_throughput", run_idx=0)
    executor = TransactionalFLEExecutor(env)
    run_id = datetime.now(UTC).strftime("curriculum-%Y%m%dT%H%M%SZ")
    journal = ResearchJournal(run_id)
    evolution = journal.state["evolution"]
    champion = evolution.get("champion") or {}
    champion_configuration = (
        champion.get("configuration", {})
        if isinstance(champion, dict)
        else {}
    )
    genome = challenger_genome(
        attempt=int(evolution.get("generation", 1) or 1),
        champion_configuration=champion_configuration,
        default_exploration=exploration,
    )
    effective_exploration = genome.placement_exploration
    turn_penalty = genome.routing_turn_penalty
    evolution["challenger"]["configuration"].update(
        {
            **genome.to_dict(),
            "placement_episodes": placement_episodes,
            "seed": seed,
        }
    )
    journal.event(
        "mutation",
        "Evolutionary challenger genome selected.",
        configuration=evolution["challenger"]["configuration"],
    )

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
            exploration=effective_exploration,
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
        logistics: dict[str, Any] | None = None
        belt_smelt_ok = False
        if smelting_ok:
            logistics = stage_astar_logistics(
                executor,
                env,
                journal,
                center=center,
                settle_seconds=logistics_settle,
                turn_penalty=turn_penalty,
            )
        if logistics is not None:
            belt_smelt_ok = stage_belt_smelting(
                executor,
                env,
                journal,
                logistics=logistics,
                settle_seconds=belt_smelt_settle,
            )

        achieved: set[str] = set()
        coal_ok = False
        copper_ok = False
        copper_smelt_ok = False
        survival_ok = False
        power_ok = False
        manufacturing_ok = False
        science_ok = False
        coal_center: tuple[float, float] | None = None
        copper_center: tuple[float, float] | None = None

        if belt_smelt_ok:
            achieved.add("iron_backbone")
            update_engineering_frontier(journal, achieved=achieved)
            coal_ok, coal_center = stage_coal_mining(
                executor,
                env,
                journal,
                settle_seconds=coal_mine_settle,
            )
        if coal_ok:
            achieved.add("coal_mining")
            update_engineering_frontier(journal, achieved=achieved)
        if belt_smelt_ok and coal_ok:
            copper_ok, copper_center = stage_copper_mining(
                executor,
                env,
                journal,
                settle_seconds=copper_mine_settle,
            )
        if copper_ok and copper_center is not None:
            achieved.add("copper_mining")
            update_engineering_frontier(journal, achieved=achieved)
            if coal_ok:
                copper_smelt_ok = stage_copper_smelting(
                    executor,
                    env,
                    journal,
                    center=copper_center,
                    settle_seconds=copper_smelt_settle,
                )
        if copper_smelt_ok:
            achieved.add("copper_smelting")
            update_engineering_frontier(journal, achieved=achieved)

        if (
            belt_smelt_ok
            and coal_ok
            and copper_smelt_ok
            and coal_center is not None
            and copper_center is not None
        ):
            survival_ok = stage_capability_survival(
                executor,
                env,
                journal,
                iron_center=center,
                coal_center=coal_center,
                copper_center=copper_center,
                settle_seconds=20,
            )

        if survival_ok:
            power_ok = stage_steam_power(
                executor,
                env,
                journal,
                settle_seconds=12,
            )
        if power_ok:
            achieved.add("steam_power")
            update_engineering_frontier(journal, achieved=achieved)
            manufacturing_ok = stage_powered_manufacturing(
                executor,
                env,
                journal,
                settle_seconds=14,
            )
        if manufacturing_ok:
            science_ok = stage_automation_science(
                executor,
                env,
                journal,
                settle_seconds=20,
            )
        if science_ok:
            achieved.add("automation_science")

        progression = update_engineering_frontier(
            journal,
            achieved=achieved,
        )

        selection = finalize_evolution_selection(
            journal,
            achieved=achieved,
        )
        final_status = (
            "generation_complete"
            if (
                belt_smelt_ok
                and coal_ok
                and copper_ok
                and copper_smelt_ok
                and survival_ok
                and power_ok
                and manufacturing_ok
                and science_ok
            )
            else "partial_success"
        )
        next_goal = progression.get("next_goal")
        if isinstance(next_goal, dict):
            next_action = (
                f"engineering frontier: {next_goal.get('label', next_goal.get('goal_id'))}"
            )
        elif belt_smelt_ok:
            next_action = "expand the production-engineering goal catalog"
        elif logistics is not None:
            next_action = "repair buffered belt-to-furnace integration"
        elif smelting_ok:
            next_action = "repair A* logistics geometry from the rejected route"
        else:
            next_action = "run alternate smelting-geometry repair experiment"
        if selection.get("promoted"):
            next_action = "champion promoted · " + next_action
        else:
            next_action = "incumbent retained · " + next_action
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
    parser.add_argument("--logistics-settle", type=int, default=30)
    parser.add_argument("--belt-smelt-settle", type=int, default=32)
    parser.add_argument("--coal-mine-settle", type=int, default=24)
    parser.add_argument("--copper-mine-settle", type=int, default=16)
    parser.add_argument("--copper-smelt-settle", type=int, default=24)
    parser.add_argument("--exploration", type=float, default=2.0)
    args = parser.parse_args()

    result = run_curriculum(
        seed=args.seed,
        placement_episodes=args.placement_episodes,
        baseline_settle=args.baseline_settle,
        trial_settle=args.trial_settle,
        scale_settle=args.scale_settle,
        smelt_settle=args.smelt_settle,
        logistics_settle=args.logistics_settle,
        belt_smelt_settle=args.belt_smelt_settle,
        coal_mine_settle=args.coal_mine_settle,
        copper_mine_settle=args.copper_mine_settle,
        copper_smelt_settle=args.copper_smelt_settle,
        exploration=args.exploration,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
