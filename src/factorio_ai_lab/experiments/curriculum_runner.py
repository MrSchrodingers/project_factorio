from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from random import Random
from typing import Any

from factorio_ai_lab.agents.evolution_advisor import propose_evolution_advice
from factorio_ai_lab.agents.llm_router import default_free_router
from factorio_ai_lab.domain.state import GridPoint
from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    fast_reposition,
    list_environments,
)
from factorio_ai_lab.learning.archive import ArchiveSchemaError, NicheArchive
from factorio_ai_lab.learning.bandit import UCB1Bandit
from factorio_ai_lab.learning.evolution import apply_advice, challenger_genome
from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.learning.knowledge import (
    KnowledgeRecall,
    recall_not_consulted,
    recall_verified_lessons,
    verify_generated_knowledge,
)
from factorio_ai_lab.learning.spatial_policy import SpatialPolicy, route_cost
from factorio_ai_lab.learning.survival import (
    FitnessVector,
    InheritedCapabilities,
    compare_challenger,
    fitness_from_research,
)
from factorio_ai_lab.metrics.rates import normalized_rate_ratio, rate_per_second
from factorio_ai_lab.planning.astar import RouteResult, RoutingWeights, weighted_astar
from factorio_ai_lab.planning.factorio_catalog import (
    EARLY_GAME_PRODUCTION_PLANNER,
    FACTORIO_DATA_VERSION,
)
from factorio_ai_lab.planning.footprints import (
    blocked_tiles,
    entity_footprint,
    entity_name,
    prototype_footprints,
)
from factorio_ai_lab.planning.fuel import (
    BURNER_MINING_DRILL,
    STONE_FURNACE,
    observed_window_seconds,
)
from factorio_ai_lab.planning.placement import (
    OUTCOME_BUILD,
    PlacementPlan,
    WorldSurvey,
    entity_position,
    footprint_tiles,
    plan_cell_placement,
    snap_to_grid,
)
from factorio_ai_lab.planning.progression import (
    DEFAULT_ENGINEERING_PLANNER,
    EngineeringState,
)
from factorio_ai_lab.planning.resupply import (
    REFUSAL_NO_FUEL_IN_WORLD,
    REFUSAL_NO_SPARE_CONTAINER,
    ContainerRole,
    ContainerSalvage,
    FuelSource,
    SmeltingOption,
    SupplyPlan,
    container_roles,
    plan_supply,
)
from factorio_ai_lab.runtime import FactorioWorldLease

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
RESEARCH_STATE = RUNS_DIR / "research_state.json"
KNOWLEDGE_LOG = RUNS_DIR / "knowledge.jsonl"
ACTIVE_RUN = RUNS_DIR / "active_run.json"
RESEARCH_HISTORY = RUNS_DIR / "research"
SPATIAL_DEMOS = RUNS_DIR / "datasets" / "spatial_demonstrations.jsonl"
EVOLUTION_CHAMPION = RUNS_DIR / "evolution_champion.json"
EVOLUTION_HISTORY = RUNS_DIR / "evolution_history.jsonl"
GENERATION_REPORTS = RUNS_DIR / "generation_reports"
NICHE_ARCHIVE = RUNS_DIR / "niche_archive.json"

#: Where the parent of a mutation came from, as written in the report.
PARENT_SOURCE_ARCHIVE = "niche_archive"
PARENT_SOURCE_CHAMPION = "global_champion"

#: Why the archive did not supply the parent, when it did not. Kept
#: explicit so a generation bred from the champion can be told from one
#: bred from an elite without rereading the archive as it stands later.
PARENT_FALLBACK_NO_ARCHIVE = "archive_unavailable"
PARENT_FALLBACK_EMPTY_ARCHIVE = "archive_empty"
PARENT_FALLBACK_NO_CONFIGURATION = "sampled_elite_has_no_configuration"

#: Mixed into the parent-sampling stream so that stream stays independent
#: of every other draw made from the same run seed.
PARENT_SAMPLING_LABEL = "niche_archive_parent_v1"

THROUGHPUT_EQUIVALENCE_TOLERANCE = 1.0

#: Which knowledge-log `stage` slug each curriculum stage writes its lessons
#: under. The slugs are the ones synthesize_lesson is called with; stages that
#: synthesize no lesson are absent on purpose, and a bottleneck on one of them
#: falls back to the most recent verified lessons under a declared basis.
KNOWLEDGE_STAGE_BY_CURRICULUM_NAME: dict[str, str] = {
    "Baseline iron mining": "baseline_mining",
    "Online placement learning": "online_placement_learning",
    "Smelting probe": "smelting_probe",
    "A* belt logistics": "astar_belt_logistics",
    "Belt-fed smelting": "belt_fed_smelting",
    "Coal self-sufficiency": "coal_self_sufficiency",
    "Copper expansion": "copper_mining",
    "Copper smelting": "copper_smelting",
    "Automation science": "automation_science",
}

#: How many recalled lessons one generation decides with. The advisor payload
#: is budgeted in characters, so more lessons would mean shorter lessons.
KNOWLEDGE_RECALL_LIMIT = 3


PLACEMENT_ARMS: dict[str, tuple[float, float]] = {
    "east_near": (4.5, 0.0),
    "west_near": (-4.5, 0.0),
    "north_near": (0.0, -5.5),
    "south_near": (0.0, 5.5),
    "northwest_edge": (-9.5, -8.5),
    "southeast_edge": (9.5, 8.5),
}

#: How far a blocked placement may be moved, in tiles, before the stage
#: refuses it. Six tiles clears a 2x2 drill plus the belt lane the earlier
#: stages leave beside it, and stays inside the measured iron patch.
PLACEMENT_SCAN_REACH = 6

#: A placement trial builds its own cell or it measures nothing: adopting the
#: drill an ancestor left on the arm would hand this genome the ancestor's
#: flow as if it were its own reading.
TRIAL_ADOPTS_NOTHING: frozenset[str] = frozenset()

#: How far above a cell's drill the supply step aims the furnace it smelts a
#: container in. The furnace is planned against a world the cell is not
#: standing in yet, so a plan anchored on the cell resolves to the cell's own
#: tiles; the engine is then asked for ``exact=False`` and puts the furnace
#: on whatever is free nearest to them, which includes the tile the chest
#: needs and fails the cell one line later. Five tiles clears the 2x2 drill,
#: the 2x2 furnace and the chest row below the drill.
SUPPLY_FURNACE_CLEARANCE = 5


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


def code_revision(*, root: Path | None = None) -> dict[str, Any]:
    """Which revision of this repository produced the generation.

    The loop runs for hours while the repository is still being worked on, so
    a series of generations can span several versions of the selection rule,
    the factory graph and the runtime catalogue. Without the revision in the
    record, a change in the numbers cannot be attributed: an improvement that
    came from a code change and one that came from evolution read the same.

    A dirty tree is part of the fact, not an embarrassment to omit --
    generation 37 was promoted under uncommitted code and its report says
    nothing about it. When the revision cannot be read at all, every field
    answers None with a stated reason; a plausible-looking string would be
    worse than no answer.
    """
    directory = Path(root) if root is not None else Path(__file__).resolve().parents[3]

    def _git(*args: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "-C", str(directory), *args],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _git.reason = f"git unavailable: {type(exc).__name__}"
            return None
        if done.returncode != 0:
            _git.reason = (done.stderr or "").strip().splitlines()[:1] or ["git refused"]
            _git.reason = _git.reason[0] if isinstance(_git.reason, list) else _git.reason
            return None
        return done.stdout.strip()

    _git.reason = None
    commit = _git("rev-parse", "HEAD")
    if commit is None:
        return {
            "commit": None,
            "branch": None,
            "dirty": None,
            "reason": _git.reason or "no repository at this path",
        }
    status = _git("status", "--porcelain")
    return {
        "commit": commit,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        # None when the tree could not be inspected: unknown is not clean.
        "dirty": None if status is None else bool(status),
        "reason": None,
    }


#: No placement episode was marked valid, so no arm carries a measurement.
NO_MEASURED_ARM = "no_measured_arm"

#: No arm had free tiles within reach, so no arm could be trialled at all.
#: Distinct from NO_MEASURED_ARM: there the trials ran and measured nothing.
NO_BUILDABLE_ARM = "no_buildable_arm"

#: The world holds neither the fuel nor a spare container, so no trial cell
#: could be assembled at all. Distinct from both of the above: the tiles were
#: free and the bandit had arms to pull. Said here because the engine's own
#: message names inventories and nothing about where supply was looked for.
NO_TRIAL_SUPPLY = "no_trial_supply_in_world"

#: What a placement trial's output number is. The world production counter
#: is not it: a rejected step rewinds that counter, so every trial reads the
#: flow of the inherited factory over the step window, identically for every
#: arm, whether or not its own placement succeeded.
TRIAL_OUTPUT_BASIS = "trial_cell_chest_contents"

#: What the smelting probe counts as its output. The world plate counter is
#: not that: the inherited factory carries furnaces of its own, and a reading
#: taken off the global counter would credit this generation with whatever
#: they happen to smelt during the window.
SMELTING_OUTPUT_BASIS = "probe_furnace_contents"

#: What the resource stages count as their output. The same reasoning, for
#: the three cells built after the iron backbone: the inherited factory mines
#: coal at (27, 9), mines copper at (-58, 83) and smelts in furnaces of its
#: own, and all of that moves the world counters while these stages run. Each
#: stage reads the container or furnace it placed itself instead.
COAL_OUTPUT_BASIS = "coal_cell_chest_contents"
COPPER_ORE_OUTPUT_BASIS = "copper_cell_chest_contents"
COPPER_PLATE_OUTPUT_BASIS = "copper_cell_furnace_contents"

#: What the belt-fed smelting stage counts as its output, for the same reason
#: once more, and for one closer to home: the world plate counter over that
#: window also carries the probe furnace stage 3 of this same generation left
#: burning, so the belt cell was being credited with the direct cell's plates
#: and then compared against them. The belt furnace is placed by this stage
#: and read before and after its own window.
BELT_SMELTING_OUTPUT_BASIS = "belt_furnace_contents"

#: The stage carries bootstrap coal it may not let the new drill burn, and
#: the standing world offered nowhere to park it. Named rather than silently
#: skipped: quarantine is what makes the endogenous-fuel claim a measurement,
#: so a window run without it would prove nothing about the cell.
NO_QUARANTINE_IN_WORLD = "no_container_to_quarantine_bootstrap_coal"


class PlacementNotMeasured(RuntimeError):
    """No placement arm produced a valid episode.

    A distinct type so the caller can tell "the bandit learned nothing this
    run" from a genuine failure of the stage. The message names the cause;
    the previous behaviour raised ValueError from max() on an empty dict,
    whose text said nothing about placement.
    """


@dataclass(frozen=True)
class ArmSelection:
    """Which arm won, why none did, and the evidence behind the choice.

    The intermediate sets are part of the result, not scratch values: the
    journal publishes them so a later reader can tell an arm that won on
    throughput from one that won on compactness among equals.
    """

    arm: str | None
    reason: str | None = None
    mean_output_by_arm: Mapping[str, float] = field(default_factory=dict)
    equivalent_throughput_arms: tuple[str, ...] = ()
    compact_candidates: tuple[str, ...] = ()


def best_compact_arm(
    *,
    arms: Mapping[str, tuple[float, float]],
    history: Sequence[Mapping[str, Any]],
) -> ArmSelection:
    """Pick the nearest arm among those with equivalent measured throughput.

    Only episodes marked valid carry a measurement. When none of them does,
    the answer is that no arm was measured -- not a crash, and not a default
    arm, which would hand the next generation a placement nothing supports.
    """
    output_by_arm: dict[str, list[float]] = {arm: [] for arm in arms}
    for row in history:
        if not row.get("valid"):
            continue
        arm = str(row.get("arm", ""))
        if arm in output_by_arm:
            output_by_arm[arm].append(float(row.get("output", 0.0)))

    mean_output_by_arm = {
        arm: sum(values) / len(values)
        for arm, values in output_by_arm.items()
        if values
    }
    if not mean_output_by_arm:
        return ArmSelection(arm=None, reason=NO_MEASURED_ARM)

    max_mean_output = max(mean_output_by_arm.values())
    equivalent = [
        arm
        for arm, mean_output in mean_output_by_arm.items()
        if max_mean_output - mean_output <= THROUGHPUT_EQUIVALENCE_TOLERANCE
    ]
    min_distance = min(math.hypot(*arms[arm]) for arm in equivalent)
    compact = [
        arm
        for arm in equivalent
        if math.isclose(
            math.hypot(*arms[arm]), min_distance, rel_tol=1e-9, abs_tol=1e-9
        )
    ]
    return ArmSelection(
        arm=next(arm for arm in arms if arm in compact),
        mean_output_by_arm=dict(mean_output_by_arm),
        equivalent_throughput_arms=tuple(equivalent),
        compact_candidates=tuple(compact),
    )


def incumbent_champion() -> dict[str, Any]:
    return read_json_object(EVOLUTION_CHAMPION)


def load_niche_archive() -> tuple[NicheArchive | None, dict[str, Any]]:
    """
    Read the niche archive, answering None when the file cannot be read.

    A missing file is a first run and answers an empty archive. A file that
    exists and does not parse answers None and the reason: the generation
    still runs, bred from the global champion, and nothing is written over
    that file, because overwriting it would replace elites that are still on
    disk with an archive starting from nothing.
    """
    try:
        archive = NicheArchive.load(NICHE_ARCHIVE)
    except ArchiveSchemaError as exc:
        return None, {
            "archive_path": str(NICHE_ARCHIVE),
            "archive_status": "unreadable",
            "archive_detail": str(exc),
        }
    return archive, {
        "archive_path": str(NICHE_ARCHIVE),
        "archive_status": "loaded",
    }


def parent_sampling_rng(*, generation: int, seed: int) -> Random:
    """
    The stream the parent draw consumes, fixed by the run seed and generation.

    Seeded from a string so label, seed and generation are mixed by SHA-512
    instead of added: neighbouring generations of one run must not draw
    correlated parents. Drawing from the global rng instead would make the
    parent depend on how many numbers the rest of the process happened to
    consume, and a run whose parent cannot be replayed from its seed cannot
    be replayed at all.
    """
    return Random(f"{PARENT_SAMPLING_LABEL}:{seed}:{generation}")


def select_mutation_parent(
    *,
    champion_configuration: dict[str, Any],
    archive: NicheArchive | None,
    rng: Random,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Choose the configuration the next mutation departs from.

    The archive answers first: a uniform draw over occupied niches spends the
    generation on a behaviour that is under-explored instead of always on the
    lineage holding the global record, which is what keeps the search moving
    when the champion installs a floor no challenger clears.

    The global champion stays the fallback for the cases the archive cannot
    serve -- no archive, an empty one, or an elite whose record carries no
    configuration to breed from -- so the loop behaves exactly as before until
    the archive has something to offer.

    Answers the configuration and where it came from. The caller records the
    provenance, because a result that cannot be attributed to its parent says
    nothing about that parent.
    """
    fallback = dict(champion_configuration or {})
    if archive is None:
        return fallback, {
            "source": PARENT_SOURCE_CHAMPION,
            "reason": PARENT_FALLBACK_NO_ARCHIVE,
            "niche_count": 0,
            "descriptor": None,
        }
    niche_count = len(archive)
    elite = archive.sample_parent(rng)
    if elite is None:
        return fallback, {
            "source": PARENT_SOURCE_CHAMPION,
            "reason": PARENT_FALLBACK_EMPTY_ARCHIVE,
            "niche_count": niche_count,
            "descriptor": None,
        }
    descriptor = elite.descriptor.to_dict()
    configuration = elite.record.get("configuration")
    if not isinstance(configuration, dict) or not configuration:
        return fallback, {
            "source": PARENT_SOURCE_CHAMPION,
            "reason": PARENT_FALLBACK_NO_CONFIGURATION,
            "niche_count": niche_count,
            "descriptor": descriptor,
        }
    return dict(configuration), {
        "source": PARENT_SOURCE_ARCHIVE,
        "reason": None,
        "niche_count": niche_count,
        "descriptor": descriptor,
        "run_id": elite.record.get("run_id"),
        "generation": elite.record.get("generation"),
    }


def patch_center(patch: Any) -> tuple[float, float]:
    box = patch.bounding_box
    return (
        (float(box.left_top.x) + float(box.right_bottom.x)) / 2.0,
        (float(box.left_top.y) + float(box.right_bottom.y)) / 2.0,
    )


def patch_bounds(patch: Any) -> tuple[float, float, float, float] | None:
    """Resource patch bounding box, as ``(left, top, right, bottom)``.

    A mining cell moved off the patch places and mines nothing, so the box
    bounds how far a blocked placement may be moved. Answers None when the
    patch cannot be read: a search must not be narrowed to a box nobody
    measured.

    On the lab map this box is exact rather than approximate. All 624 tiles
    of (15.5, 70.5)-(38.5, 95.5) carry iron ore, measured over RCON with zero
    holes, so "inside the box" and "on the ore" are the same statement here.
    """
    try:
        box = patch.bounding_box
        return (
            float(box.left_top.x),
            float(box.left_top.y),
            float(box.right_bottom.x),
            float(box.right_bottom.y),
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


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
                {
                    "name": "Electronic circuits",
                    "status": "pending",
                    "detail": (
                        "Build a powered copper-cable-to-electronic-circuit chain "
                        "and validate intermediate production."
                    ),
                },
                {
                    "name": "Logistic science",
                    "status": "pending",
                    "detail": (
                        "Manufacture belts and inserters electrically and consume "
                        "them in a powered logistic-science assembler."
                    ),
                },
                {
                    "name": "Transactional rebuild optimization",
                    "status": "pending",
                    "detail": (
                        "Demolish a dominated logistics branch only when a compact "
                        "survivor retains throughput; otherwise rollback the destruction."
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
                    "evidence_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
                "required": ["lesson", "next_hypothesis", "evidence_keys"],
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
                        "Do not invent rates, counts, ratios, durations or units. "
                        "List the exact top-level fact keys supporting the claims "
                        "in evidence_keys. Keep each prose field under 35 words."
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
        generated_lesson = parsed["lesson"]
        generated_hypothesis = parsed["next_hypothesis"]
        evidence_keys = parsed["evidence_keys"]
        verification = verify_generated_knowledge(
            lesson=generated_lesson,
            hypothesis=generated_hypothesis,
            facts=facts,
            evidence_keys=evidence_keys,
        )
        provider = result.get("_router")
        if verification.verified:
            lesson = generated_lesson
            hypothesis = generated_hypothesis
            source = "llm_verified"
        else:
            lesson = fallback_lesson
            hypothesis = fallback_hypothesis
            source = "deterministic_fallback"
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        provider = {"error": f"{type(exc).__name__}: {exc}"}
        evidence_keys = []
        verification = verify_generated_knowledge(
            lesson=fallback_lesson,
            hypothesis=fallback_hypothesis,
            facts=facts,
            evidence_keys=[],
        )
        source = "deterministic_fallback"

    record = {
        "at": utc_now(),
        "stage": stage,
        "lesson": lesson,
        "next_hypothesis": hypothesis,
        "facts": facts,
        "provider": provider,
        "source": source,
        "evidence_keys": evidence_keys,
        "verification": verification.to_dict(),
    }
    append_jsonl(KNOWLEDGE_LOG, record)
    return record



# A stage spends far longer in the world than the literal it passes to
# sleep(): the game keeps running through move_to, extract_item and
# place_entity. Measured across the copper stage, the window was roughly 80
# game seconds against a literal of 16. This is the overhead to budget fuel
# for; the denominator itself is always the observed window, never this.
STAGE_OVERHEAD_SECONDS = 90.0

# How long the world keeps running across a whole generation, in game seconds.
# Generation 27 was measured live at roughly 4134 game seconds while still
# mid-run at tick 4682161, and the arena runs at game speed 10
# (fle/env/gym_env/registry.py:143), which is what turns the 415-443 s of
# wall-clock duration recorded in runs/generation_reports into that figure.
# Fuel that has to outlast the stage placing it is sized against this horizon
# and never against a settle literal: 20 coal, the largest dose in the
# curriculum, keep a burner drill alive for 533 s, an eighth of a generation.
LAB_GENERATION_HORIZON_SECONDS = 4200.0

#: Burner machines that must keep burning for the rest of the generation once
#: they exist, paired with the label the journal reports them under. The left
#: element is the variable the FLE namespace carries across stages, which is
#: how stage_capability_survival already reaches these same machines. Order is
#: the fuel priority when the released stock cannot cover every machine: the
#: coal drill first, because it is the only one that turns the stock back into
#: a flow, then the boiler, whose outage also stops every electric assembler,
#: then the ore drills the measured iron and copper stages read from.
FUEL_FED_MACHINES: tuple[tuple[str, str], ...] = (
    ("coal_drill", "coal_drill"),
    ("boiler", "boiler"),
    ("copper_drill", "copper_drill"),
    ("drill", "iron_baseline_drill"),
    ("scale_drill", "iron_scale_drill"),
    ("smelt_drill", "iron_smelt_drill"),
    ("logistics_drill", "iron_logistics_drill"),
)

#: Coal one burner inserter is primed with. It refuels itself from the fuel it
#: goes on to move, so one unit is the whole primer and the rest of a
#: machine's charge belongs in the chest behind it.
FUEL_FEED_PRIMER_COAL = 1

#: Coal left standing in a container that already feeds a chain. Taking fuel
#: out of a container is not dismantling it -- stages 1, 2, 6 and 7 already do
#: it and the container keeps standing -- but emptying the one an inserter
#: pulls from stops the machine on the far end, and that outage would be read
#: as this generation's regression. The reserve is one burner machine's charge
#: for the rest of the generation, so only the surplus above what the standing
#: chain still has to burn is offered to a draw.
FUEL_CHAIN_RESERVE_COAL = BURNER_MINING_DRILL.coal_for_seconds(
    LAB_GENERATION_HORIZON_SECONDS
)


def _game_ticks(env: Any) -> int | None:
    """Elapsed game ticks, or None when the counter is unavailable."""
    try:
        return int(env.unwrapped.instance.get_elapsed_ticks())
    except (AttributeError, OSError, TypeError, ValueError):
        return None


class _StageClock:
    """Game clock bracketing the step a stage measures.

    `stop` belongs inside the acceptance callback. A rejected step is rolled
    back by resetting the environment to the checkpoint taken before it ran,
    which rewinds the tick counter, so reading the clock after `execute`
    returns would throw away the window that was just measured.
    """

    def __init__(self, env: Any) -> None:
        self._env = env
        self.ticks_before = _game_ticks(env)
        self.ticks_after: int | None = None

    def stop(self) -> None:
        self.ticks_after = _game_ticks(self._env)

    def window(self, fallback_seconds: float) -> float:
        return observed_window_seconds(
            self.ticks_before,
            self.ticks_after,
            fallback_seconds,
        )

    def source(self) -> str:
        """Which instrument produced the window.

        Mirrors the fallback branches of `observed_window_seconds`: without
        two readings, or with a counter that did not advance, the window is
        the sleep literal and has to be labelled as one.
        """
        if (
            self.ticks_before is None
            or self.ticks_after is None
            or self.ticks_after <= self.ticks_before
        ):
            return "sleep_literal"
        return "observed_game_ticks"


def _record_observed_window(
    journal: ResearchJournal,
    clock: _StageClock,
    *,
    metric_prefix: str,
    fallback_seconds: float,
) -> float:
    """Window a stage actually ran for, journalled with its instrument.

    The game keeps running through move_to, extract_item and place_entity, so
    dividing output by the sleep literal inflated every rate by the ratio
    between the two windows -- roughly five-fold where it was measured. Stages
    divide by the returned window and record where it came from, so rates
    taken with different instruments are never compared as if they were the
    same measurement.
    """
    window = clock.window(fallback_seconds)
    journal.state["metrics"][f"{metric_prefix}_duration_s"] = window
    journal.state["metrics"][f"{metric_prefix}_duration_source"] = clock.source()
    return window


def _namespace_measure(namespace: Any, key: str) -> float | None:
    """Value the remote script assigned to `key`, or None when it never did.

    A missing attribute means the script aborted before that assignment.
    Defaulting it to 0.0 makes an abort indistinguishable from a real
    measurement of zero, which is how `circuit_iron_ore: 0.0` was read for
    eleven generations as an empty iron buffer while the buffer held ~137 ore.
    """
    raw = getattr(namespace, key, None)
    return None if raw is None else float(raw or 0.0)


def _measured_at_least(measured: dict[str, Any], key: str, threshold: float) -> bool:
    """True only when the script reported a number that clears `threshold`.

    An unmeasured key clears no gate: it carries no evidence in either
    direction, and comparing it numerically would raise instead of decide.
    """
    value = measured.get(key)
    return isinstance(value, (int, float)) and float(value) >= threshold


def _measured_above(
    measured: dict[str, Any],
    key: str,
    threshold: float = 0.0,
) -> bool:
    """True only when the script reported a number strictly above `threshold`."""
    value = measured.get(key)
    return isinstance(value, (int, float)) and float(value) > threshold


def _cell_total(measured: dict[str, Any], *keys: str) -> float | None:
    """Sum of readings taken off the cell this generation built, or None.

    One unread key makes the whole sum unmeasured. A step that aborted
    halfway through its window did not produce the part it managed to
    report, and summing what survives would turn an abort into a smaller
    measurement instead of an absent one.
    """
    total = 0.0
    for key in keys:
        value = measured.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        total += float(value)
    return total


def _recorded_metric(journal: Any, key: str) -> float | None:
    """A number an earlier stage recorded, or None when no stage recorded it.

    ``metrics.get(key, 0.0)`` is the substitution this project has paid for
    repeatedly: a baseline that was never measured became a baseline of zero,
    and ``rate_per_second`` then raised ``duration_s must be positive`` from
    inside a stage that had already done its work.
    """
    value = journal.state.get("metrics", {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _measured_text(measured: dict[str, Any], key: str, spec: str = ".0f") -> str:
    """Measurement rendered for prose, or a marker when it was never taken."""
    value = measured.get(key)
    if not isinstance(value, (int, float)):
        return "unmeasured"
    return format(float(value), spec)


def production_output(namespace: Any, item: str) -> float:
    stats = namespace._get_production_stats()
    return float(stats.get("output", {}).get(item, 0.0))


#: Prototype the baseline mining cell is made of. Stated once so the survey
#: below and the construction script cannot drift apart.
BASELINE_DRILL_NAME = BURNER_MINING_DRILL.name

#: The container a mining cell drops its ore into.
BASELINE_CHEST_NAME = "wooden-chest"

#: The same container under the name an FLE script builds it by. Stated next
#: to its entity name because the two are read in different places -- the
#: survey matches entities by name, the script asks for a prototype -- and a
#: pair that drifts apart surveys one container and places another.
BASELINE_CHEST_PROTOTYPE = "WoodenChest"

#: What a burner mining cell burns, under both names, for the same reason.
MINING_CELL_FUEL_ITEM = "coal"
MINING_CELL_FUEL_PROTOTYPE = "Coal"

#: The container a stage makes when the standing world holds no spare one.
#: Measured over RCON on 2026-09-23 against the recipes this run has
#: enabled: `iron-chest` is one of them, costs 8 iron plates and is crafted
#: by hand; `iron-plate` is one iron ore smelted for 3.2 s. The heir of
#: generation 37 carries 24 iron ore and 7 stone furnaces and no chest at
#: all, which is the case this exists for.
SMELTED_CHEST_NAME = "iron-chest"
SMELTED_CHEST_PROTOTYPE = "IronChest"
CONTAINER_PLATES = 8
CONTAINER_SMELT_SECONDS = 26

#: Every container a cell may drop into, under the prototype name a script
#: places it by. Stated once because the two are read in different places --
#: the survey matches entities by name, the script asks for a prototype --
#: and a pair that drifts apart measures one container and places another. A
#: name absent from here is placed as the baseline chest, which fails loudly
#: at place_entity instead of quietly measuring the wrong container.
CONTAINER_PROTOTYPES: dict[str, str] = {
    BASELINE_CHEST_NAME: BASELINE_CHEST_PROTOTYPE,
    SMELTED_CHEST_NAME: SMELTED_CHEST_PROTOTYPE,
}

#: The inserter that lifts ore off the belt into the terminal container.
LOGISTICS_INSERTER_NAME = "burner-inserter"

#: What a smelting cell is made of besides the drill, under both names.
SMELTING_FURNACE_NAME = "stone-furnace"
SMELTING_FURNACE_PROTOTYPE = "StoneFurnace"

#: The ore a container is smelted from and the plate it becomes, under both
#: names for the same reason.
SMELT_ORE_ITEM = "iron-ore"
SMELT_ORE_PROTOTYPE = "IronOre"
SMELT_PLATE_ITEM = "iron-plate"
SMELT_PLATE_PROTOTYPE = "IronPlate"

#: What the burner inserter of the belt line is charged with. The dose the
#: curriculum has always used, kept as a floor for the same reason the mining
#: doses are: an inserter that stops mid-window measures fuel, not logistics.
LOGISTICS_INSERTER_COAL = 10

#: How the journal records the state of a stage's supply plan. A world that
#: could not be read is not a world that holds nothing: the first leaves the
#: stage on the path it took before any of this existed, the second is a
#: measurement, and only the second justifies refusing.
SUPPLY_PLANNED = "planned"
SUPPLY_REFUSED = "refused"
SUPPLY_WORLD_UNREAD = "world_not_surveyed"

#: Coal the curriculum has always put into a trial cell and into the promoted
#: cell. Kept as the floor of a sized charge, never as the charge itself: a
#: dose that stops covering the window turns a throughput reading into a fuel
#: reading, which is what `planning.fuel` exists to prevent.
TRIAL_COAL_FLOOR = 12
SCALE_COAL_FLOOR = 20
SMELT_COAL_FLOOR = 20

#: Coal one container smelt burns, derived from the furnace draw over the
#: window the smelt takes instead of a number written here.
CONTAINER_SMELT_FUEL = STONE_FURNACE.coal_for_seconds(
    float(CONTAINER_SMELT_SECONDS)
)

#: Refusals a stage cannot build around: the world holds no fuel at all, or
#: no container at all. A world merely short of fuel is a different reading
#: -- the stage draws what is there, inserts what it drew and records the
#: shortfall, which is a measurement rather than an assumption.
BLOCKING_REFUSALS: frozenset[str] = frozenset(
    {REFUSAL_NO_FUEL_IN_WORLD, REFUSAL_NO_SPARE_CONTAINER}
)


def survey_world(env: Any) -> WorldSurvey | None:
    """Everything standing in the world, with its footprints resolved.

    One read serves every placement a stage plans, so all the arms of a stage
    are decided against the same world, and the same seed replays the same
    placements. Answers None when the world cannot be read: planning on a
    world nobody surveyed is not evidence that the tiles were free, and the
    caller has to record it as the blind build it is.
    """
    unwrapped = getattr(env, "unwrapped", env)
    instance = getattr(unwrapped, "instance", None)
    try:
        entities = instance.namespace._save_entity_state(
            distance=500,
            player_entities=True,
            resource_entities=False,
            items_on_ground=False,
            encode=False,
            compress=False,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    return WorldSurvey(
        entities=tuple(
            entity for entity in (entities or ()) if isinstance(entity, Mapping)
        ),
        footprints=_runtime_entity_footprints(instance),
    )


def mining_cell_reserve(tiles: frozenset[GridPoint]) -> tuple[GridPoint, ...]:
    """The tile a mining cell needs besides the drill footprint itself.

    ``place_entity_next_to(Prototype.WoodenChest, drill.position,
    Direction.DOWN)`` lands on the tile under the right column of the drill:
    the inherited world holds drill (27, 83) with its chest at (27.5, 84.5)
    and drill (32, 83) with its chest at (32.5, 84.5). Planning the drill
    alone would accept tiles whose output side is the belt lane, and the
    chest placement would then fail the whole step.
    """
    if not tiles:
        return ()
    return (
        GridPoint(
            max(tile.x for tile in tiles),
            max(tile.y for tile in tiles) + 1,
        ),
    )


def tiles_center(tiles: Collection[GridPoint]) -> tuple[float, float]:
    """Centre of a rectangular tile set, in world coordinates."""
    left = min(tile.x for tile in tiles)
    right = max(tile.x for tile in tiles)
    top = min(tile.y for tile in tiles)
    bottom = max(tile.y for tile in tiles)
    return ((left + right + 1) / 2, (top + bottom + 1) / 2)


def smelting_furnace_anchor(
    drill_position: tuple[float, float],
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> tuple[float, float]:
    """Where ``place_entity_next_to(StoneFurnace, drill.position, DOWN)`` lands.

    FLE offsets the new entity by ``ceil(ref_height + entity_height) / 2``
    from the reference centre and the engine then snaps it to the grid
    (fle/env/tools/agent/place_entity_next_to/server.lua:179). For the 2x2
    drill and the 2x2 furnace of this cell that is two tiles down, which is
    where the inherited cell stands: drill (27, 73), furnace (27, 75),
    measured off the promoted checkpoint on 2026-09-23. Deriving it keeps the
    reserve below and the adoption check honest if either footprint changes.
    """
    drill = entity_footprint(
        {"name": BASELINE_DRILL_NAME, "direction": 0},
        footprints,
    )
    furnace = entity_footprint(
        {"name": SMELTING_FURNACE_NAME, "direction": 0},
        footprints,
    )
    offset = math.ceil(drill[1] + furnace[1]) / 2
    return snap_to_grid(
        (drill_position[0], drill_position[1] + offset),
        furnace,
    )


def smelting_cell_reserve(
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> Callable[[frozenset[GridPoint]], Iterable[GridPoint]]:
    """The tiles a smelting cell needs besides the drill's own footprint.

    A drill placed where the furnace cannot follow fails the whole step one
    line later, at ``place_entity_next_to``, with the same message the drill
    placement would have given. Planning both together is what makes the
    shift meaningful.
    """

    def reserve(tiles: frozenset[GridPoint]) -> tuple[GridPoint, ...]:
        if not tiles:
            return ()
        return tuple(
            footprint_tiles(
                entity=SMELTING_FURNACE_NAME,
                position=smelting_furnace_anchor(
                    tiles_center(tiles),
                    footprints,
                ),
                footprints=footprints,
            )
        )

    return reserve


def plan_smelting_cell(
    survey: WorldSurvey | None,
    anchor: tuple[float, float],
    *,
    reach: int = 0,
    region: tuple[float, float, float, float] | None = None,
) -> PlacementPlan:
    """Where the smelting probe's drill goes, given the world as it stands.

    Nothing is adopted. The probe's claim is that this generation built a
    cell that smelts, and the inherited furnaces are no basis for it: both
    of the ones standing hold a full output stack, measured at 100 iron
    plates each off the promoted checkpoint, so a furnace that was adopted
    is a furnace that has stopped. The plan moves the cell to free tiles
    instead, and what it measures there is its own.
    """
    return plan_cell_placement(
        survey,
        anchor,
        entity=BASELINE_DRILL_NAME,
        adopt_names=TRIAL_ADOPTS_NOTHING,
        reach=reach,
        region=region,
        extra_tiles=smelting_cell_reserve(
            None if survey is None else survey.footprints
        ),
    )


def plan_smelting_furnace(
    survey: WorldSurvey | None,
    drill_position: tuple[float, float],
) -> PlacementPlan:
    """Whether the tiles under the cell's furnace are really free.

    No reach and nothing adopted: the script places the furnace with
    ``place_entity_next_to``, so the only tiles that decide anything are the
    ones the engine will use, and a furnace standing on them is a furnace
    this generation did not build. The drill plan already reserved these
    tiles; this is the reading that says so in the journal.
    """
    return plan_cell_placement(
        survey,
        smelting_furnace_anchor(
            drill_position,
            None if survey is None else survey.footprints,
        ),
        entity=SMELTING_FURNACE_NAME,
        adopt_names=TRIAL_ADOPTS_NOTHING,
    )


@dataclass(frozen=True)
class LogisticsGeometry:
    """Where the belt line of the logistics stage runs and unloads.

    Stated once, as offsets from the drill, because four places depend on
    it: the route endpoints, the terminal inserter and chest, the tiles the
    placement reserves, and the furnace the next stage extends into. A copy
    that drifts would reserve one set of tiles and build on another.
    """

    start: GridPoint
    goal: GridPoint
    inserter: tuple[float, float]
    chest: tuple[float, float]
    downstream_inserter: tuple[float, float]
    downstream_furnace: tuple[float, float]


def logistics_geometry(drill_position: tuple[float, float]) -> LogisticsGeometry:
    """The belt line and its terminal, derived from where the drill stands.

    The downstream pair is where ``stage_belt_smelting`` puts its inserter
    and furnace: one tile east of the chest, and then the position
    ``place_entity_next_to`` computes from that inserter.
    """
    start_world = (drill_position[0] + 0.5, drill_position[1] + 1.5)
    goal_world = (drill_position[0] + 5.5, drill_position[1] + 4.5)
    goal = GridPoint(round(goal_world[0] - 0.5), round(goal_world[1] - 0.5))
    return LogisticsGeometry(
        start=GridPoint(round(start_world[0] - 0.5), round(start_world[1] - 0.5)),
        goal=goal,
        inserter=(goal.x + 1.5, goal.y + 0.5),
        chest=(goal.x + 2.5, goal.y + 0.5),
        downstream_inserter=(goal.x + 3.5, goal.y + 0.5),
        downstream_furnace=(goal.x + 5.0, goal.y + 1.0),
    )


def logistics_cell_reserve(
    footprints: Mapping[str, tuple[int, int]] | None = None,
) -> Callable[[frozenset[GridPoint]], Iterable[GridPoint]]:
    """Tiles the belt line needs besides the drill, given the drill footprint.

    The route itself is planned around what is standing, so only the fixed
    points are reserved: where the belt starts and ends, the inserter and
    chest at the terminal, and the two entities the next stage extends the
    terminal with. Both containers this curriculum places are one tile, so
    the chest is reserved under the baseline name whichever one the supply
    plan ends up handing over.
    """

    def reserve(tiles: frozenset[GridPoint]) -> tuple[GridPoint, ...]:
        if not tiles:
            return ()
        geometry = logistics_geometry(tiles_center(tiles))
        reserved: set[GridPoint] = {geometry.start, geometry.goal}
        for position, name in (
            (geometry.inserter, LOGISTICS_INSERTER_NAME),
            (geometry.chest, BASELINE_CHEST_NAME),
            (geometry.downstream_inserter, LOGISTICS_INSERTER_NAME),
            (geometry.downstream_furnace, SMELTING_FURNACE_NAME),
        ):
            reserved |= footprint_tiles(
                entity=name,
                position=position,
                footprints=footprints,
            )
        return tuple(reserved)

    return reserve


def plan_mining_cell(
    survey: WorldSurvey | None,
    anchor: tuple[float, float],
    *,
    adopt_names: Collection[str] | None = None,
    reach: int = 0,
    region: tuple[float, float, float, float] | None = None,
) -> PlacementPlan:
    """Where the next mining cell goes, given the world as it stands."""
    return plan_cell_placement(
        survey,
        anchor,
        entity=BASELINE_DRILL_NAME,
        adopt_names=adopt_names,
        reach=reach,
        region=region,
        extra_tiles=mining_cell_reserve,
    )


def container_prototype(name: str) -> str:
    """FLE prototype name for a container entity name."""
    return CONTAINER_PROTOTYPES.get(name, BASELINE_CHEST_PROTOTYPE)


def cell_container(plan: SupplyPlan | None) -> str:
    """Which container the cell this plan supplies will be built out of.

    The plan names one only when it obtained one: a salvaged chest is the
    kind that was standing, a smelted one is the kind the recipe makes, and
    a stage that already carried what it needed keeps the baseline.
    """
    if plan is None:
        return BASELINE_CHEST_NAME
    return plan.container_name or BASELINE_CHEST_NAME


def blocking_refusals(plan: SupplyPlan | None) -> tuple[str, ...]:
    """The refusals of a plan that leave a stage with nothing to run on.

    A world that holds no fuel at all, or no container at all, stops the
    stage; a world merely short of fuel does not. Keeping the two apart is
    what lets a stage draw eleven coal and say so instead of refusing a
    window it could have measured.
    """
    if plan is None:
        return ()
    return tuple(
        refusal for refusal in plan.refusals if refusal in BLOCKING_REFUSALS
    )


def container_positions(
    survey: WorldSurvey | None,
    *,
    names: Collection[str] = tuple(CONTAINER_PROTOTYPES),
) -> frozenset[tuple[float, float]]:
    """Where the containers stood before a stage placed anything.

    What a trial built has to stay distinguishable from what it inherited:
    the ancestor's chests hold ore this generation did not mine, and summing
    them into a trial reading would credit the arm with the ancestor's
    factory. Every container kind a cell may be built of is read, not only
    the baseline one, or a cell built out of a smelted chest would be
    measured as if the world had always held it.
    """
    if survey is None:
        return frozenset()
    found: set[tuple[float, float]] = set()
    for entity in survey.entities:
        if entity_name(entity) not in names:
            continue
        position = entity_position(entity)
        if position is not None:
            found.add(position)
    return frozenset(found)


def cell_yield(
    env: Any,
    *,
    inherited_containers: Collection[tuple[float, float]],
    item: str,
    container_name: str = BASELINE_CHEST_NAME,
) -> float | None:
    """How much ``item`` sits in containers that were not standing before.

    The cell a trial built is the only thing that trial may be credited with.
    The world production counter is not that: the rollback of a rejected step
    rewinds it, so every trial of generation 37 read the same 0 -> 95 from
    the ancestor's drills, including the trials whose placement the engine
    refused.

    Answers None when the world cannot be read or no new container stands.
    An unmeasured trial is not a trial that measured zero.
    """
    survey = survey_world(env)
    if survey is None:
        return None
    unwrapped = getattr(env, "unwrapped", env)
    instance = getattr(unwrapped, "instance", None)
    total = 0.0
    measured = False
    for entity in survey.entities:
        if entity_name(entity) != container_name:
            continue
        position = entity_position(entity)
        if position is None or position in inherited_containers:
            continue
        measured = True
        total += float(
            _chest_item_count(
                instance,
                x=position[0],
                y=position[1],
                item=item,
                container=container_name,
            )
        )
    return total if measured else None


def inherited_mining_cell(
    survey: WorldSurvey | None,
    center: tuple[float, float],
) -> PlacementPlan | None:
    """The mining cell an ancestor already left on the baseline tiles.

    Lifelong inheritance restores the promoted factory before the curriculum
    runs, and ``patch_center`` is deterministic for a fixed map, so an heir
    computes the same tiles its ancestor already built on. ``place_entity``
    answers "entity already exists at the target position" there, FLE marks
    the step failed on that text, and stage 0 raises for every heir until the
    checkpoint changes.

    The decision is the placement layer's: occupancy is read as tiles, never
    as a radius or a centre comparison, because a 2x2 drill one tile off
    shares half the baseline footprint and still blocks the placement. Only
    the ``adopt`` outcome is an inherited cell; a plan to build, or a refusal,
    both mean there is nothing standing here to commission. A world that
    could not be surveyed plans to build, which leaves the caller on the
    construction path it took before inheritance existed.

    The whole plan is answered, tiles included, because a mining cell is more
    than its drill: :func:`inherited_cell_container` reads the container off
    those tiles, and the commissioning script has to bind it under the name
    the construction script binds.
    """
    plan = plan_mining_cell(survey, center)
    return plan if plan.adopts else None


#: How far from an adopted drill a container still counts as that cell's own
#: output chest, in tiles. ``mining_cell_reserve`` states the tile the
#: construction path puts it on; the reach covers a cell whose chest stands a
#: tile off that, and stops short of the next cell -- the inherited world
#: holds the baseline cell at (27, 83) and the scaled one at (32, 83).
BASELINE_CONTAINER_REACH = 2.0


def inherited_cell_container(
    survey: WorldSurvey | None,
    drill_tiles: Collection[GridPoint],
) -> tuple[tuple[float, float], str] | None:
    """The container the adopted mining cell already drops its ore into.

    The construction path binds ``chest`` when it places one; the adoption
    path bound nothing, and generation 44 reached stage 13 and died there on
    ``NameError: name 'chest' is not defined``, with the electronic-circuit
    chain and the green-science chain both reading that name. The two paths
    have to leave the same names behind, so the adopted cell's container is
    read off the tile the construction path would have put it on:
    ``mining_cell_reserve`` states that tile once, for both.

    A container covering that tile wins; failing that, the nearest container
    within ``BASELINE_CONTAINER_REACH`` of the cell, so a chest rebuilt one
    tile over is still recognised as this cell's. Answers None when the world
    was not surveyed or holds no container there -- which is a reading, not a
    chest measured to be empty, and the caller binds ``chest`` to None so the
    name exists either way.
    """
    if survey is None or not drill_tiles:
        return None
    tiles = frozenset(drill_tiles)
    reserved = set(mining_cell_reserve(tiles))
    anchor = tiles_center(tiles)
    best: tuple[tuple[bool, float, float, float], tuple[float, float], str] | None
    best = None
    for entity in survey.entities:
        name = entity_name(entity)
        if name not in CONTAINER_PROTOTYPES:
            continue
        position = entity_position(entity)
        if position is None:
            continue
        covers = bool(
            footprint_tiles(
                entity=name,
                position=position,
                footprints=survey.footprints,
            )
            & reserved
        )
        distance = math.hypot(position[0] - anchor[0], position[1] - anchor[1])
        if not covers and distance > BASELINE_CONTAINER_REACH:
            continue
        key = (not covers, distance, position[1], position[0])
        if best is None or key < best[0]:
            best = (key, position, name)
    return None if best is None else (best[1], best[2])


def survey_stage_supply(
    env: Any,
    *,
    anchor: tuple[float, float],
    fuel_needed: int,
    container_needed: bool,
    container_name: str = BASELINE_CHEST_NAME,
    fuel_item: str = MINING_CELL_FUEL_ITEM,
    chain_reserve: int = 0,
) -> SupplyPlan | None:
    """What the standing world can give this stage, read off the world.

    An heir carries whatever its ancestor happened to be holding when it was
    promoted, which for generation 38 was ``coal: 8`` and no container at
    all. The kit a mining cell is assembled from therefore has to be found
    rather than assumed, and the only honest place to find it is the world as
    it stands: the containers that hold fuel, and the containers no material
    edge touches.

    When the world holds no spare container the survey offers the second
    source: the ore and the furnace the heir carries, which
    :func:`plan_supply` turns into a container this generation smelted. That
    case is not hypothetical -- the single unattached chest is committed by
    stage 2, so the generation after the next promotion finds none.

    ``chain_reserve`` is how much fuel a container that feeds a chain keeps
    for itself. A draw is not a dismantling -- the container stays standing,
    and every stage here already draws from one -- but a draw that empties the
    container an inserter pulls from stops the machine behind it, and a caller
    asking for more than the world holds would do exactly that. Zero, the
    default, is the draw every stage has always made.

    Answers None when the world, or the agent's own inventory, could not be
    read. That is not a world measured to be empty: it leaves the caller on
    the path it took before any of this existed, and the caller records the
    unsurveyed step as what it is.
    """
    survey = survey_world(env)
    if survey is None:
        return None
    unwrapped = getattr(env, "unwrapped", env)
    instance = getattr(unwrapped, "instance", None)
    fuel_carried = _carried_item_count(instance, fuel_item)
    containers_carried = _carried_item_count(instance, container_name)
    if fuel_carried is None or containers_carried is None:
        return None

    graph = build_factory_graph(list(survey.entities))
    sources: list[FuelSource] = []
    spares: list[ContainerSalvage] = []
    for role in container_roles(graph):
        available = _chest_item_count(
            instance,
            x=role.position[0],
            y=role.position[1],
            item=fuel_item,
            container=role.name,
        )
        # Only the surplus above the reserve is a source. A container that
        # feeds nothing offers everything it holds; one an inserter pulls from
        # keeps what the chain behind it still has to burn.
        spare = available
        if role.supplies_chain:
            spare = max(0, available - max(0, int(chain_reserve)))
        if spare > 0:
            sources.append(
                FuelSource(
                    position=role.position,
                    available=spare,
                    supplies_chain=role.supplies_chain,
                )
            )
        if role.unattached and role.name == container_name:
            spares.append(
                ContainerSalvage(
                    position=role.position,
                    name=role.name,
                    holding=_chest_item_count(
                        instance,
                        x=role.position[0],
                        y=role.position[1],
                        item=None,
                        container=role.name,
                    ),
                )
            )
    smelting: SmeltingOption | None = None
    if container_needed:
        ore_carried = _carried_item_count(instance, SMELT_ORE_ITEM)
        plates_carried = _carried_item_count(instance, SMELT_PLATE_ITEM)
        furnaces_carried = _carried_item_count(instance, SMELTING_FURNACE_NAME)
        if ore_carried is None or plates_carried is None or furnaces_carried is None:
            return None
        # The furnace is one this generation places. An inherited furnace
        # holds the ancestor's plates in its output, and extracting from it
        # would carry that stock into this generation's inventory as though
        # this generation had smelted it.
        furnace_plan = plan_cell_placement(
            survey,
            anchor,
            entity=SMELTING_FURNACE_NAME,
            adopt_names=TRIAL_ADOPTS_NOTHING,
            reach=PLACEMENT_SCAN_REACH,
        )
        smelting = SmeltingOption(
            container_name=SMELTED_CHEST_NAME,
            plates_needed=CONTAINER_PLATES,
            ore_carried=ore_carried,
            plates_carried=plates_carried,
            furnaces_carried=furnaces_carried,
            furnace_position=furnace_plan.position if furnace_plan.builds else None,
            fuel_per_smelt=CONTAINER_SMELT_FUEL,
            seconds=CONTAINER_SMELT_SECONDS,
        )

    return plan_supply(
        anchor=anchor,
        fuel_needed=fuel_needed,
        fuel_carried=fuel_carried,
        fuel_sources=tuple(sources),
        container_needed=container_needed,
        containers_carried=containers_carried,
        spare_containers=tuple(spares),
        smelting=smelting,
    )


def supply_report(plan: SupplyPlan | None) -> dict[str, Any]:
    """The supply plan as the journal records it, its absence included."""
    if plan is None:
        return {"status": SUPPLY_WORLD_UNREAD}
    return {
        "status": SUPPLY_REFUSED if plan.refused else SUPPLY_PLANNED,
        **plan.to_dict(),
    }


def quarantine_container(
    survey: WorldSurvey | None,
    *,
    anchor: tuple[float, float],
    exclude: Collection[tuple[float, float]] = (),
) -> ContainerRole | None:
    """Where this stage may park the bootstrap coal it is still carrying.

    The coal stage claims the cell it builds runs on coal that cell mined, and
    the claim is only a measurement if the coal the heir arrived with is out
    of the drill's reach first. Until now the quarantine was a chest this
    stage placed, and an heir carries none: generation 41 aborted at
    ``place_entity(Prototype.WoodenChest, ...)`` with ``bootstrap_total``
    0.0 and every later reading unmeasured.

    A container the world already holds serves the same purpose and costs no
    game time to make. Ordered by ``(supplies a chain, distance, position)``,
    so coal is parked in a container no machine is waiting on before one that
    feeds something, and among equals the nearest wins. ``exclude`` drops the
    container the supply plan means to carry off, because a chest filled here
    and picked up two lines later hands its contents straight back.

    Nothing is taken and nothing is emptied. The stage only inserts, so no
    standing chain loses the supply it was built around.
    """
    if survey is None:
        return None
    skip = {(float(x), float(y)) for x, y in exclude}
    roles = [
        role
        for role in container_roles(build_factory_graph(list(survey.entities)))
        if role.position not in skip
    ]
    if not roles:
        return None
    return min(
        roles,
        key=lambda role: (
            role.supplies_chain,
            math.hypot(role.position[0] - anchor[0], role.position[1] - anchor[1]),
            role.position[1],
            role.position[0],
        ),
    )


def mining_cell_supply_script(
    plan: SupplyPlan | None,
    *,
    fuel_needed: int,
) -> str:
    """FLE script that draws a mining cell's kit out of the standing world.

    Belongs after the cell's drill is placed, so a placement the engine
    refuses costs the world nothing: the step aborts before any container is
    touched, and a trial that never ran cannot be charged with a withdrawal.

    Each draw is clamped twice, by what the container was measured to hold
    and by what the agent is still missing, because the second clamp is the
    one that survives a container an earlier step already emptied. Each call
    is wrapped on its own: one container that will not answer must not take
    down the draws that would have worked.

    A plan that smelts its container adds a furnace, one charge of coal, the
    ore and the seconds the smelt takes, and then crafts the chest. It is
    placed with ``exact=False``: the drill of this cell is already standing
    when the prelude runs, so a furnace aimed at tiles planned before that
    placement lets the engine step aside instead of failing the step.

    Whatever the engine says about a refused call is neutralised before it
    travels in the payload. FLE marks a step failed on the substring ``error``
    anywhere in what the script printed (fle/env/gym_env/environment.py:451),
    so a message quoted verbatim would fail a step that had already done its
    work. The counters are always declared, including for a world that could
    not be surveyed, so the payload never reads a name the script never bound.
    """
    lines = [
        "supply_fuel_drawn=0",
        "supply_fuel_log=[]",
        "supply_container_recovered=0",
        "supply_container_smelted=0",
        "supply_note=''",
    ]
    if plan is None:
        return "\n".join(lines)
    note = (
        "    supply_note=(supply_note+' '+str(supply_exc)[:80])[:240]"
        ".replace('rror','rr0r').replace('xception','xcepti0n')"
    )
    # Clamped against what the plan says the step needs, not against the
    # dose alone: a plan that smelts a container has to draw the furnace's
    # charge too, and a clamp that ignored it would leave the drill short at
    # the far end of the window.
    charge = int(plan.fuel_needed) if plan.fuel_needed else int(fuel_needed)
    for draw in plan.fuel_draws:
        x, y = draw.position
        lines.append(
            "try:\n"
            f"    supply_short=max(0,{charge}"
            f"-inspect_inventory()[Prototype.{MINING_CELL_FUEL_PROTOTYPE}])\n"
            "    if supply_short>0:\n"
            f"        supply_taken=extract_item(Prototype.{MINING_CELL_FUEL_PROTOTYPE},"
            f"Position(x={x},y={y}),quantity=min(supply_short,{int(draw.quantity)}))\n"
            "        supply_fuel_drawn+=supply_taken\n"
            f"        supply_fuel_log.append(({x},{y},supply_taken))\n"
            "except Exception as supply_exc:\n"
            f"    supply_fuel_log.append(({x},{y},0))\n" + note
        )
    salvage = plan.salvage
    if salvage is not None:
        x, y = salvage.position
        lines.append(
            "try:\n"
            f"    if inspect_inventory()[Prototype.{BASELINE_CHEST_PROTOTYPE}]<1:\n"
            f"        pickup_entity(Prototype.{BASELINE_CHEST_PROTOTYPE},"
            f"Position(x={x},y={y}))\n"
            "        supply_container_recovered=1\n"
            "except Exception as supply_exc:\n" + note
        )
    smelt = plan.smelt
    if smelt is not None and smelt.ore_to_smelt > 0 and smelt.position is not None:
        x, y = smelt.position
        chest = container_prototype(smelt.container_name)
        lines.append(
            "try:\n"
            f"    if inspect_inventory()[Prototype.{chest}]<1:\n"
            f"        supply_furnace=place_entity(Prototype."
            f"{SMELTING_FURNACE_PROTOTYPE},"
            f"position=Position(x={x},y={y}),exact=False)\n"
            f"        supply_smelt_coal=min({int(smelt.fuel_to_insert)},"
            f"inspect_inventory()[Prototype.{MINING_CELL_FUEL_PROTOTYPE}])\n"
            "        if supply_smelt_coal>0:\n"
            f"            supply_furnace=insert_item(Prototype."
            f"{MINING_CELL_FUEL_PROTOTYPE},supply_furnace,"
            "quantity=supply_smelt_coal)\n"
            f"        supply_smelt_ore=min({int(smelt.ore_to_smelt)},"
            f"inspect_inventory()[Prototype.{SMELT_ORE_PROTOTYPE}])\n"
            "        if supply_smelt_ore>0:\n"
            f"            supply_furnace=insert_item(Prototype."
            f"{SMELT_ORE_PROTOTYPE},supply_furnace,quantity=supply_smelt_ore)\n"
            f"            sleep({int(smelt.seconds)})\n"
            f"            extract_item(Prototype.{SMELT_PLATE_PROTOTYPE},"
            "supply_furnace.position,quantity=supply_smelt_ore)\n"
            f"        if inspect_inventory()[Prototype.{SMELT_PLATE_PROTOTYPE}]"
            f">={int(smelt.plates_needed)}:\n"
            f"            supply_container_smelted=craft_item(Prototype.{chest},"
            "quantity=1)\n"
            "except Exception as supply_exc:\n" + note
        )
    return "\n".join(lines)


def coal_quarantine_script(
    vault: ContainerRole | None,
    *,
    keep: int,
) -> str:
    """FLE script that puts the bootstrap coal this stage may not burn away.

    ``keep`` is everything the step still has to spend, not the seed alone:
    smelting the cell's container burns a charge of its own, so a quarantine
    sized to the seed would leave the furnace and the drill bidding for the
    same single coal and the drill would stand unfuelled through a window
    already paid for. Only the drill ever receives the seed, and it receives
    it by a line that asks for exactly one.

    Belongs after the cell's drill is placed and before any fuel is drawn: a
    placement the engine refuses then costs the world nothing, and the draw
    that follows sizes itself against an inventory the quarantine has already
    emptied, so the stage never draws coal it is about to park.

    The counters are declared even when there is nowhere to park anything,
    so the payload never reads a name the script never bound. The vault name
    is bound to the container only when coal really went into it: the release
    script downstream must not reopen a quarantine that never happened.

    Whatever the engine says about a refused call is neutralised before it
    travels: FLE marks a step failed on the substring ``error`` anywhere in
    what the script printed (fle/env/gym_env/environment.py:451).
    """
    lines = [
        "bootstrap_total=inspect_inventory()[Prototype.Coal]",
        f"bootstrap_surplus=max(0,bootstrap_total-{max(0, int(keep))})",
        "bootstrap_quarantine=0",
        "bootstrap_vault=None",
        "bootstrap_note=''",
    ]
    if vault is None:
        return "\n".join(lines)
    x, y = vault.position
    prototype = container_prototype(vault.name)
    lines.append(
        "try:\n"
        "    if bootstrap_surplus>0:\n"
        f"        bootstrap_vault=get_entity(Prototype.{prototype},"
        f"Position(x={x},y={y}))\n"
        "        if bootstrap_vault is not None:\n"
        "            bootstrap_vault=insert_item(Prototype.Coal,bootstrap_vault,"
        "quantity=bootstrap_surplus)\n"
        "            bootstrap_quarantine=bootstrap_surplus\n"
        "except Exception as coal_setup_exc:\n"
        "    bootstrap_note=str(coal_setup_exc)[:80]"
        ".replace('rror','rr0r').replace('xception','xcepti0n')"
    )
    return "\n".join(lines)


#: Coal the baseline cell is charged with, on either path. The cell runs on
#: it for the whole settle window; the figure is the one the curriculum has
#: always used and is kept identical between the two paths so the window they
#: measure is the same window.
BASELINE_DRILL_COAL = 20


def baseline_build_script(
    *,
    center: tuple[float, float],
    settle_seconds: int,
) -> str:
    """FLE script that builds the baseline mining cell from nothing.

    Binds ``drill`` and ``chest``: every later stage that reaches into the
    baseline cell reads those two names out of the FLE namespace, and
    :func:`baseline_adopt_script` binds the same two for the same reason.
    """
    return f"""
drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={center[0]}, y={center[1]}),
    direction=Direction.DOWN,
)
drill = insert_item(Prototype.Coal, drill, quantity={BASELINE_DRILL_COAL})
chest = place_entity_next_to(
    Prototype.WoodenChest,
    drill.position,
    direction=Direction.DOWN,
)
sleep({settle_seconds})
print({{'chest_inventory': inspect_inventory(chest)}})
"""


def baseline_adopt_script(
    *,
    drill_position: tuple[float, float],
    container: tuple[tuple[float, float], str] | None,
    settle_seconds: int,
) -> str:
    """FLE script that commissions the mining cell the world already holds.

    Placing here is what stalled the loop -- the tiles are taken -- so the
    cell is fuelled and measured instead of rebuilt.

    It binds the names :func:`baseline_build_script` binds, and that parity is
    the contract: a name that exists only when the cell was built is a trap
    for every stage downstream. Generation 44 walked into it at stage 13,
    ``NameError: name 'chest' is not defined``, with stages 13 and 14 both
    reading the name. A world with no container on the cell's tiles binds
    ``chest`` to None, which a stage can test; a name that was never bound
    cannot be tested at all.
    """
    if container is None:
        chest_binding = "chest = None"
    else:
        position, name = container
        chest_binding = (
            "chest = get_entity(\n"
            f"    Prototype.{container_prototype(name)},\n"
            f"    Position(x={position[0]}, y={position[1]}),\n"
            ")"
        )
    return f"""
drill = get_entity(
    Prototype.BurnerMiningDrill,
    Position(x={drill_position[0]}, y={drill_position[1]}),
)
drill = insert_item(Prototype.Coal, drill, quantity={BASELINE_DRILL_COAL})
{chest_binding}
sleep({settle_seconds})
print({{'drill_fuel': inspect_inventory(drill)}})
"""


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
    # One survey serves both decisions: which cell is standing here, and which
    # container it drops into. Two reads would decide against two worlds.
    survey = survey_world(env)
    inherited_cell = inherited_mining_cell(survey, center)
    inherited_drill = None if inherited_cell is None else inherited_cell.position
    inherited_container = (
        None
        if inherited_cell is None
        else inherited_cell_container(survey, inherited_cell.tiles)
    )
    journal.state["next_action"] = (
        "commission the inherited mining cell"
        if inherited_drill is not None
        else "build burner drill and chest"
    )
    journal.flush()

    clock = _StageClock(env)
    measurement: dict[str, float] = {}

    def accept_baseline(result: Any) -> bool:
        clock.stop()
        iron_output = production_output(namespace, "iron-ore")
        measurement["iron_output"] = iron_output
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and iron_output > 0
        )

    if inherited_drill is not None:
        # The cell is already standing and the tiles are taken. Fuel it and
        # measure it: placing here is exactly what stalled the loop, and
        # rebuilding what already works would produce no evidence anyway.
        code = baseline_adopt_script(
            drill_position=inherited_drill,
            container=inherited_container,
            settle_seconds=settle_seconds,
        )
    else:
        code = baseline_build_script(
            center=center,
            settle_seconds=settle_seconds,
        )
    step = executor.execute(
        code,
        accept=accept_baseline,
        use_checkpoint_for_action=False,
    )
    if not step.accepted:
        raise RuntimeError(
            "inherited baseline mining cell did not produce iron"
            if inherited_drill is not None
            else "baseline mining cell did not produce iron"
        )

    # An inherited cell is measured under its own metric prefix. The flow is
    # real, but the achievement is the ancestor's, and
    # survival.fitness_from_research credits baseline_iron_rate_per_s as
    # endogenous output of this genome. Writing the inherited number there
    # would report inheritance as production, which is the exact failure mode
    # inherited_capabilities exists to prevent.
    inherited = inherited_drill is not None
    metric_prefix = "inherited_iron" if inherited else "baseline_iron"
    window = _record_observed_window(
        journal,
        clock,
        metric_prefix=metric_prefix,
        fallback_seconds=float(settle_seconds),
    )
    iron_rate = rate_per_second(measurement["iron_output"], window)
    journal.state["metrics"]["baseline_cell_origin"] = (
        "inherited" if inherited else "built"
    )
    if inherited:
        journal.state["metrics"]["inherited_iron_output"] = measurement["iron_output"]
        journal.state["metrics"]["inherited_iron_rate_per_s"] = iron_rate
        journal.state["metrics"]["inherited_baseline_reward"] = step.reward
        # Which container the commissioned cell delivers into, under the name
        # the stages downstream read it by. None is a reading: the cell's
        # tiles were surveyed and held no container, and ``chest`` was bound
        # to None rather than left undefined.
        journal.state["metrics"]["inherited_baseline_container"] = (
            None
            if inherited_container is None
            else {
                "x": inherited_container[0][0],
                "y": inherited_container[0][1],
                "name": inherited_container[1],
            }
        )
    else:
        journal.state["metrics"]["baseline_iron_output"] = measurement["iron_output"]
        journal.state["metrics"]["baseline_iron_rate_per_s"] = iron_rate
        journal.state["metrics"]["baseline_reward"] = step.reward
    journal.complete_stage(
        0,
        (
            f"Inherited mining cell adopted, {measurement['iron_output']:.0f} iron "
            "ore measured and not credited to this generation."
            if inherited
            else f"Baseline cell accepted with {measurement['iron_output']:.0f} "
            "iron ore output."
        ),
    )
    journal.event(
        "accept",
        (
            "Baseline mining cell was already standing from the inherited factory."
            if inherited
            else "Baseline drill-to-chest mining cell accepted."
        ),
        reward=step.reward,
        iron_output=measurement["iron_output"],
        cell_origin="inherited" if inherited else "built",
    )
    lesson = synthesize_lesson(
        stage="baseline_mining",
        facts={
            "iron_output": measurement["iron_output"],
            "reward": step.reward,
            "entities_added": 0 if inherited else 2,
            "cell_origin": "inherited" if inherited else "built",
            "placement": {"x": center[0], "y": center[1]},
        },
        fallback_lesson=(
            "A burner drill inherited on the measured iron patch keeps "
            "producing without being rebuilt, so the stage measures it "
            "instead of placing over it."
            if inherited
            else "A burner drill placed inside the measured iron patch and "
            "aligned to a chest produces validated iron output."
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
    radius_scale: float,
    region: tuple[float, float, float, float] | None = None,
) -> str:
    namespace = env.unwrapped.instance.namespace
    champion = journal.state.get("evolution", {}).get("champion") or {}
    champion_config = (
        champion.get("configuration", {})
        if isinstance(champion, dict)
        else {}
    )
    incumbent_arm = champion_config.get("placement_best_arm")
    scaled_arms = {
        arm: (dx * radius_scale, dy * radius_scale)
        for arm, (dx, dy) in PLACEMENT_ARMS.items()
    }
    arm_order = list(scaled_arms)
    if incumbent_arm in PLACEMENT_ARMS:
        arm_order.remove(incumbent_arm)
        arm_order.insert(0, incumbent_arm)
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

    # One survey decides every arm. Each trial is rolled back, so the world
    # the next arm is planned against is the world this one was planned
    # against; planning them from a single reading is what makes the whole
    # stage replayable from the run seed.
    survey = survey_world(env)
    inherited_containers = container_positions(survey)
    plans = {
        arm: plan_mining_cell(
            survey,
            (center[0] + dx, center[1] + dy),
            adopt_names=TRIAL_ADOPTS_NOTHING,
            reach=PLACEMENT_SCAN_REACH,
            region=region,
        )
        for arm, (dx, dy) in scaled_arms.items()
    }
    online["placement_plans"] = {
        arm: plan.to_dict() for arm, plan in plans.items()
    }
    online["trial_output_basis"] = TRIAL_OUTPUT_BASIS
    buildable = [arm for arm in arm_order if plans[arm].outcome == OUTCOME_BUILD]
    withheld = {
        arm: plans[arm].reason
        for arm in arm_order
        if plans[arm].outcome != OUTCOME_BUILD
    }
    online["withheld_arms"] = withheld

    journal.event(
        "learning",
        "Online UCB1 placement learning started on the live Factorio engine.",
        episodes=episodes,
        arms=scaled_arms,
        radius_scale=radius_scale,
        buildable_arms=buildable,
        withheld_arms=withheld,
    )
    if not buildable:
        # Every arm is boxed in by what is already standing. That is a
        # reading about the world, said here rather than left for UCB1 to
        # raise over an empty arm set.
        journal.state.setdefault("metrics", {})[
            "placement_selection_outcome"
        ] = NO_BUILDABLE_ARM
        online["status"] = NO_BUILDABLE_ARM
        journal.flush()
        raise PlacementNotMeasured(NO_BUILDABLE_ARM)

    # A trial that runs dry mid-window measures fuel, not mining speed, so
    # the charge is sized from the window the step really spends in the world
    # and the curriculum's dose is kept as its floor. The kit that charge is
    # spent on is not in the inventory an heir starts with, so it is looked
    # for in the world. Planned once, against the reading the placements were
    # planned against: every trial is rolled back, so the world the next one
    # meets is the world this one was planned against.
    trial_dose = max(
        TRIAL_COAL_FLOOR,
        BURNER_MINING_DRILL.coal_for_seconds(
            float(settle_seconds) + STAGE_OVERHEAD_SECONDS
        ),
    )
    supply = survey_stage_supply(
        env,
        anchor=center,
        fuel_needed=trial_dose,
        container_needed=True,
    )
    online["supply"] = supply_report(supply)
    journal.state["metrics"]["placement_trial_supply"] = online["supply"]
    journal.state["metrics"]["placement_trial_coal_dose"] = trial_dose
    if supply is not None and supply.refused:
        # Neither the fuel nor a spare container is out there. That is a
        # reading about the world, and it is said here: running the episodes
        # anyway spends the whole stage to arrive at an engine message about
        # inventories that names nothing about where supply was looked for.
        journal.state.setdefault("metrics", {})[
            "placement_selection_outcome"
        ] = NO_TRIAL_SUPPLY
        online["status"] = NO_TRIAL_SUPPLY
        journal.flush()
        journal.event(
            "refusal",
            "Placement trials refused: the standing world supplies no trial cell.",
            supply=online["supply"],
        )
        raise PlacementNotMeasured(NO_TRIAL_SUPPLY)
    supply_prelude = mining_cell_supply_script(supply, fuel_needed=trial_dose)
    # Which container the cell is assembled from is a reading, not a
    # constant: the heir carries none, the world holds one spare until a
    # promotion commits it, and after that the plan smelts one. The trial
    # measures the chest it placed, so it has to look for the kind it placed.
    trial_container = cell_container(supply)
    trial_container_binding = (
        f"trial_container_type = Prototype.{container_prototype(trial_container)}"
    )
    online["container_name"] = trial_container
    journal.state["metrics"]["placement_trial_container"] = trial_container

    bandit = UCB1Bandit(tuple(buildable), exploration=exploration)

    for episode in range(episodes):
        arm = bandit.select()
        plan = plans[arm]
        dx, dy = scaled_arms[arm]
        target = plan.position or (center[0] + dx, center[1] + dy)
        fast_reposition(env, x=target[0], y=target[1])

        world_output_before = production_output(namespace, "iron-ore")
        measured: dict[str, Any] = {
            "cell_yield": None,
            "world_output_before": world_output_before,
            "world_output_after": world_output_before,
            "trial_fuel": None,
            "supply_fuel_drawn": None,
            "supply_container_recovered": None,
            "supply_container_smelted": None,
            "supply_log": None,
            "supply_note": None,
            "valid": False,
        }

        def reject_trial(
            result: Any,
            measured_state: dict[str, Any] = measured,
        ) -> bool:
            # Read before the rollback restores the checkpoint, and read off
            # the chest this trial placed. The world counter over the same
            # window also carries whatever the inherited factory produced,
            # which is the same number for every arm.
            measured_state["cell_yield"] = cell_yield(
                env,
                inherited_containers=inherited_containers,
                item="iron-ore",
                container_name=trial_container,
            )
            measured_state["world_output_after"] = production_output(
                namespace,
                "iron-ore",
            )
            measured_state["trial_fuel"] = _namespace_measure(
                namespace,
                "trial_fuel",
            )
            measured_state["supply_fuel_drawn"] = _namespace_measure(
                namespace,
                "supply_fuel_drawn",
            )
            measured_state["supply_container_recovered"] = _namespace_measure(
                namespace,
                "supply_container_recovered",
            )
            measured_state["supply_container_smelted"] = _namespace_measure(
                namespace,
                "supply_container_smelted",
            )
            measured_state["supply_log"] = _supply_log_rows(
                getattr(namespace, "supply_fuel_log", None)
            )
            note = getattr(namespace, "supply_note", None)
            measured_state["supply_note"] = (
                (str(note)[:240] or None) if note is not None else None
            )
            # A cell that was never fuelled measured nothing about its
            # placement: its chest is empty because the drill never turned,
            # not because the tiles are poor. A dose nobody measured decides
            # nothing either way and is left to the conditions above.
            measured_state["valid"] = (
                not bool(result.info.get("error_occurred"))
                and result.candidate_game_state is not None
                and measured_state["cell_yield"] is not None
                and measured_state["trial_fuel"] != 0.0
            )
            return False

        code = f"""
{trial_container_binding}
trial_drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target[0]}, y={target[1]}),
    direction=Direction.DOWN,
)
{supply_prelude}
trial_fuel = min({trial_dose}, inspect_inventory()[Prototype.Coal])
if trial_fuel > 0:
    trial_drill = insert_item(Prototype.Coal, trial_drill, quantity=trial_fuel)
trial_chest = place_entity_next_to(
    trial_container_type,
    trial_drill.position,
    direction=Direction.DOWN,
)
sleep({settle_seconds})
print({{
    'trial_inventory': inspect_inventory(trial_chest),
    'trial_fuel': trial_fuel,
    'supply_fuel_drawn': supply_fuel_drawn,
    'supply_container_recovered': supply_container_recovered,
}})
"""
        step = executor.execute(
            code,
            accept=reject_trial,
            use_checkpoint_for_action=False,
        )

        distance = math.hypot(dx, dy)
        valid = bool(measured["valid"])
        trial_output = measured["cell_yield"] if valid else None
        reward = -50.0 if trial_output is None else float(trial_output)

        bandit.update(arm, reward)
        row = {
            "episode": episode,
            "arm": arm,
            "offset": {"x": dx, "y": dy},
            "placement": plan.to_dict(),
            "reward": reward,
            # None, never 0.0: a trial that could not be measured did not
            # measure an empty chest.
            "output": None if trial_output is None else float(trial_output),
            "world_output_before": float(measured["world_output_before"]),
            "world_output_after": float(measured["world_output_after"]),
            "valid": valid,
            # Why the trial carries no measurement, in the engine's own
            # words. Without it an unmeasured arm reads the same whether the
            # tiles were taken, the script had nothing to place with, or the
            # drill simply produced nothing.
            "failure_text": _step_error_text(step.info),
            "distance": distance,
            "engine_reward": float(step.reward),
            # What this trial took out of the standing world, and what it put
            # into its own drill. None, never 0.0, when the step never got
            # that far: a withdrawal nobody measured is not a withdrawal of
            # nothing, and the ledger is what tells a generation that produced
            # from one that spent its inheritance.
            "coal_dose_target": trial_dose,
            "coal_inserted": measured["trial_fuel"],
            "supply_fuel_drawn": measured["supply_fuel_drawn"],
            "supply_container_recovered": measured["supply_container_recovered"],
            "supply_container_smelted": measured["supply_container_smelted"],
            "container_name": trial_container,
            "supply_draws": measured["supply_log"],
            "supply_note": measured["supply_note"],
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
            output=row["output"],
            valid=valid,
            placement=row["placement"],
            failure_text=row["failure_text"],
        )

    ucb_best = bandit.best_observed()
    outcome = best_compact_arm(arms=scaled_arms, history=online["history"])
    mean_output_by_arm = dict(outcome.mean_output_by_arm)
    equivalent_throughput_arms = list(outcome.equivalent_throughput_arms)
    compact_candidates = list(outcome.compact_candidates)
    if outcome.arm is None:
        # Every episode was invalid. That is a reading about the run -- no arm
        # was measured -- and it has to be said, not turned into an exception
        # whose text mentions nothing about placement.
        journal.state.setdefault("metrics", {})[
            "placement_selection_outcome"
        ] = outcome.reason
        online["status"] = outcome.reason
        journal.flush()
        raise PlacementNotMeasured(outcome.reason)
    best = outcome.arm

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
    journal.state["metrics"]["placement_radius_scale"] = radius_scale
    journal.state["metrics"]["placement_withheld_arms"] = withheld
    journal.state["metrics"]["placement_trial_output_basis"] = TRIAL_OUTPUT_BASIS
    journal.complete_stage(
        1,
        (
            f"Real trials found throughput-equivalent placements within "
            f"±{THROUGHPUT_EQUIVALENCE_TOLERANCE:.0f} item; "
            f"{best} selected by minimum placement distance "
            f"({len(buildable)} of {len(scaled_arms)} arms had free tiles)."
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
            "withheld_arms": withheld,
            "output_basis": TRIAL_OUTPUT_BASIS,
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
    radius_scale: float,
    region: tuple[float, float, float, float] | None = None,
) -> None:
    namespace = env.unwrapped.instance.namespace
    base_dx, base_dy = PLACEMENT_ARMS[best_arm]
    dx, dy = base_dx * radius_scale, base_dy * radius_scale
    # Planned again, from the same anchor and against the same world the
    # trials were planned against: the rollback of every trial leaves the
    # world untouched, so this answers the tiles the promoted arm was
    # actually measured on. The cell is this generation's, so nothing here
    # is adopted -- an inherited drill would be credited as scaled_iron,
    # which survival.fitness_from_research reads as endogenous output.
    plan = plan_mining_cell(
        survey_world(env),
        (center[0] + dx, center[1] + dy),
        adopt_names=TRIAL_ADOPTS_NOTHING,
        reach=PLACEMENT_SCAN_REACH,
        region=region,
    )
    journal.state["metrics"]["scaled_placement"] = plan.to_dict()
    if plan.position is None:
        raise RuntimeError(
            f"learned placement {best_arm} has no free tiles: {plan.reason}"
        )
    target = plan.position

    # The promoted cell is built out of the same kit the trials measured on,
    # and an heir carries none of it. Unlike a trial, this step commits, so
    # what it draws leaves the world for good and the ledger below is the
    # only record that it did.
    scale_dose = max(
        SCALE_COAL_FLOOR,
        BURNER_MINING_DRILL.coal_for_seconds(
            float(settle_seconds) + STAGE_OVERHEAD_SECONDS
        ),
    )
    supply = survey_stage_supply(
        env,
        anchor=center,
        fuel_needed=scale_dose,
        container_needed=True,
    )
    journal.state["metrics"]["scaled_supply"] = supply_report(supply)
    journal.state["metrics"]["scaled_coal_dose"] = scale_dose
    if supply is not None and supply.refused:
        raise RuntimeError(
            "learned placement cannot be supplied by the standing world: "
            + ", ".join(supply.refusals)
        )
    supply_prelude = mining_cell_supply_script(supply, fuel_needed=scale_dose)
    scale_container = cell_container(supply)
    scale_container_binding = (
        f"scale_container_type = Prototype.{container_prototype(scale_container)}"
    )
    journal.state["metrics"]["scaled_container"] = scale_container

    journal.set_stage(
        2,
        status="running",
        detail=f"Promoting learned placement {best_arm} into the persistent factory.",
        next_action="commit second mining cell",
    )
    fast_reposition(env, x=target[0], y=target[1])
    output_before = production_output(namespace, "iron-ore")
    clock = _StageClock(env)
    measured: dict[str, float] = {}
    # None until the committed step reports it. An absent reading is not a
    # withdrawal of zero, and only the ledger separates what this generation
    # built from what it spent of its inheritance.
    drawn: dict[str, Any] = {
        "coal_inserted": None,
        "supply_fuel_drawn": None,
        "supply_container_recovered": None,
        "supply_container_smelted": None,
        "supply_draws": None,
    }

    def accept_scale(result: Any) -> bool:
        clock.stop()
        output_after = production_output(namespace, "iron-ore")
        iron_output = max(0.0, output_after - output_before)
        measured["iron_output"] = iron_output
        measured["output_before"] = output_before
        measured["output_after"] = output_after
        drawn["coal_inserted"] = _namespace_measure(namespace, "scale_fuel")
        drawn["supply_fuel_drawn"] = _namespace_measure(
            namespace,
            "supply_fuel_drawn",
        )
        drawn["supply_container_recovered"] = _namespace_measure(
            namespace,
            "supply_container_recovered",
        )
        drawn["supply_container_smelted"] = _namespace_measure(
            namespace,
            "supply_container_smelted",
        )
        drawn["supply_draws"] = _supply_log_rows(
            getattr(namespace, "supply_fuel_log", None)
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and iron_output > 0
        )

    code = f"""
{scale_container_binding}
scale_drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target[0]}, y={target[1]}),
    direction=Direction.DOWN,
)
{supply_prelude}
scale_fuel = min({scale_dose}, inspect_inventory()[Prototype.Coal])
if scale_fuel > 0:
    scale_drill = insert_item(Prototype.Coal, scale_drill, quantity=scale_fuel)
scale_chest = place_entity_next_to(
    scale_container_type,
    scale_drill.position,
    direction=Direction.DOWN,
)
sleep({settle_seconds})
print({{
    'scale_inventory': inspect_inventory(scale_chest),
    'scale_fuel': scale_fuel,
    'supply_fuel_drawn': supply_fuel_drawn,
    'supply_container_recovered': supply_container_recovered,
}})
"""
    step = executor.execute(
        code,
        accept=accept_scale,
        use_checkpoint_for_action=False,
    )
    if not step.accepted:
        raise RuntimeError("learned placement failed promotion")

    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="scaled_iron",
        fallback_seconds=float(settle_seconds),
    )
    journal.state["metrics"]["scaled_iron_output"] = measured["iron_output"]
    journal.state["metrics"]["scaled_iron_rate_per_s"] = rate_per_second(
        measured["iron_output"],
        window,
    )
    journal.state["metrics"]["scaled_reward"] = step.reward
    journal.state["metrics"]["scaled_supply_drawn"] = dict(drawn)
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
    region: tuple[float, float, float, float] | None = None,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    anchor = (center[0], center[1] - 10.5)

    journal.set_stage(
        3,
        status="validating",
        detail="Testing direct burner-drill-to-furnace smelting with rollback protection.",
        next_action="probe iron-plate automation",
    )

    # The anchor is a fixed offset from a patch centre that is deterministic
    # on a fixed map, so an heir aims at the tiles its ancestor's own
    # smelting cell stands on. Generations 38 and 39 both died here, on
    # "entity already exists at the target position {x = 27, y = 73}", and
    # with them every stage the curriculum reaches through this one. The
    # decision is the placement layer's, over tiles, and it covers the
    # furnace too: a drill placed where the furnace cannot follow fails the
    # same way one line later.
    survey = survey_world(env)
    drill_plan = plan_smelting_cell(
        survey,
        anchor,
        reach=PLACEMENT_SCAN_REACH,
        region=region,
    )
    journal.state["metrics"]["smelting_drill_placement"] = drill_plan.to_dict()
    if drill_plan.position is None:
        journal.fail_stage(
            3,
            f"Smelting probe found no free tiles for its drill: {drill_plan.reason}.",
        )
        journal.event(
            "refusal",
            "Smelting probe refused: the standing factory occupies its tiles.",
            placement=drill_plan.to_dict(),
        )
        return False
    target = drill_plan.position
    furnace_plan = plan_smelting_furnace(survey, target)
    journal.state["metrics"]["smelting_furnace_placement"] = furnace_plan.to_dict()
    if furnace_plan.position is None:
        journal.fail_stage(
            3,
            f"Smelting probe found no free tiles for its furnace: {furnace_plan.reason}.",
        )
        journal.event(
            "refusal",
            "Smelting probe refused: nothing free below the drill for a furnace.",
            placement=furnace_plan.to_dict(),
        )
        return False

    # Both burners are charged out of the standing world. An heir carries
    # whatever its ancestor was holding, which by the time stage 2 has
    # committed is no coal at all: generation 39 measured `fuel_carried: 0`
    # at both of the stages before this one.
    drill_dose = max(
        SMELT_COAL_FLOOR,
        BURNER_MINING_DRILL.coal_for_seconds(
            float(settle_seconds) + STAGE_OVERHEAD_SECONDS
        ),
    )
    furnace_dose = max(
        SMELT_COAL_FLOOR,
        STONE_FURNACE.coal_for_seconds(
            float(settle_seconds) + STAGE_OVERHEAD_SECONDS
        ),
    )
    supply = survey_stage_supply(
        env,
        anchor=target,
        fuel_needed=drill_dose + furnace_dose,
        container_needed=False,
    )
    journal.state["metrics"]["smelting_supply"] = supply_report(supply)
    journal.state["metrics"]["smelting_coal_dose"] = drill_dose + furnace_dose
    blocked = blocking_refusals(supply)
    if blocked:
        journal.fail_stage(
            3,
            "Smelting probe refused: the standing world holds no fuel for it "
            f"({', '.join(blocked)}).",
        )
        journal.event(
            "refusal",
            "Smelting probe refused: no fuel in the standing world.",
            supply=journal.state["metrics"]["smelting_supply"],
        )
        return False
    supply_prelude = mining_cell_supply_script(
        supply,
        fuel_needed=drill_dose + furnace_dose,
    )

    fast_reposition(env, x=target[0], y=target[1])
    plate_before = production_output(namespace, "iron-plate")
    clock = _StageClock(env)
    measured: dict[str, Any] = {}

    def validate_smelting(result: Any) -> bool:
        clock.stop()
        plate_after = production_output(namespace, "iron-plate")
        measured["iron_plate_world_before"] = plate_before
        measured["iron_plate_world_after"] = plate_after
        # Read off the furnace this step placed. The world counter over the
        # same window also carries the inherited furnaces, and a stage that
        # took its number from there would report their flow as its own.
        measured["iron_plate_output"] = _namespace_measure(namespace, "smelt_plates")
        measured["drill_coal_inserted"] = _namespace_measure(
            namespace,
            "smelt_drill_fuel",
        )
        measured["furnace_coal_inserted"] = _namespace_measure(
            namespace,
            "smelt_furnace_fuel",
        )
        measured["supply_fuel_drawn"] = _namespace_measure(
            namespace,
            "supply_fuel_drawn",
        )
        measured["supply_draws"] = _supply_log_rows(
            getattr(namespace, "supply_fuel_log", None)
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and _measured_above(measured, "iron_plate_output")
        )

    code = f"""
smelt_drill = place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target[0]}, y={target[1]}),
    direction=Direction.DOWN,
)
{supply_prelude}
smelt_drill_fuel = min({drill_dose}, inspect_inventory()[Prototype.Coal])
if smelt_drill_fuel > 0:
    smelt_drill = insert_item(Prototype.Coal, smelt_drill, quantity=smelt_drill_fuel)
smelt_furnace = place_entity_next_to(
    Prototype.StoneFurnace,
    smelt_drill.position,
    direction=Direction.DOWN,
)
smelt_furnace_fuel = min({furnace_dose}, inspect_inventory()[Prototype.Coal])
if smelt_furnace_fuel > 0:
    smelt_furnace = insert_item(
        Prototype.Coal,
        smelt_furnace,
        quantity=smelt_furnace_fuel,
    )
smelt_plates_before = inspect_inventory(smelt_furnace)[Prototype.IronPlate]
sleep({settle_seconds})
smelt_plates = max(
    0,
    inspect_inventory(smelt_furnace)[Prototype.IronPlate] - smelt_plates_before,
)
print({{
    'furnace_inventory': inspect_inventory(smelt_furnace),
    'smelt_plates': smelt_plates,
    'smelt_drill_fuel': smelt_drill_fuel,
    'smelt_furnace_fuel': smelt_furnace_fuel,
    'supply_fuel_drawn': supply_fuel_drawn,
}})
"""
    step = executor.execute(
        code,
        accept=validate_smelting,
        use_checkpoint_for_action=False,
    )

    drawn = {
        "drill_coal_inserted": measured.get("drill_coal_inserted"),
        "furnace_coal_inserted": measured.get("furnace_coal_inserted"),
        "supply_fuel_drawn": measured.get("supply_fuel_drawn"),
        "supply_draws": measured.get("supply_draws"),
    }
    journal.state["metrics"]["smelting_supply_drawn"] = drawn

    if step.accepted:
        plates = float(measured["iron_plate_output"])
        window = _record_observed_window(
            journal,
            clock,
            metric_prefix="direct_smelting",
            fallback_seconds=float(settle_seconds),
        )
        plate_rate = rate_per_second(plates, window)
        journal.state["metrics"].update(
            {
                "iron_plate_output": plates,
                "direct_smelting_plate_rate_per_s": plate_rate,
                "smelting_cell_origin": "built",
                "smelting_plate_basis": SMELTING_OUTPUT_BASIS,
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
            duration_s=window,
            plate_rate_per_s=plate_rate,
            placement=drill_plan.to_dict(),
            output_basis=SMELTING_OUTPUT_BASIS,
        )
        lesson = synthesize_lesson(
            stage="smelting_probe",
            facts={
                "accepted": True,
                "iron_plate_output": plates,
                "duration_s": window,
                "plate_rate_per_s": plate_rate,
                "engine_reward": step.reward,
                "placement_shift": drill_plan.shift,
                "output_basis": SMELTING_OUTPUT_BASIS,
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
        # None, never 0.0: a step that aborted before the furnace was read
        # did not measure an empty furnace.
        iron_plate_output=measured.get("iron_plate_output"),
        placement=drill_plan.to_dict(),
    )
    lesson = synthesize_lesson(
        stage="smelting_probe",
        facts={
            "accepted": False,
            "iron_plate_output": measured.get("iron_plate_output"),
            "engine_reward": step.reward,
            "failure_text": _step_error_text(step.info),
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
    item: str | None,
    container: str = BASELINE_CHEST_NAME,
) -> int:
    """How much of `item` one standing container holds.

    ``item=None`` counts everything in it, which is what carrying a container
    off hands to the agent: picking a chest up takes its contents with it, so
    a salvage that reported only the fuel would hide an inherited stock
    arriving as this generation's inventory.
    """
    selector = "" if item is None else f"'{item}'"
    command = (
        "/c "
        "local p=storage.agent_characters and storage.agent_characters[1]; "
        "if not p then rcon.print('0') return end; "
        f"local e=p.surface.find_entity('{container}',{{x={x},y={y}}}); "
        "if not e then rcon.print('0') return end; "
        "local inv=e.get_inventory(defines.inventory.chest); "
        f"rcon.print(inv and inv.get_item_count({selector}) or 0)"
    )
    raw = instance.rcon_client.send_command(command)
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def _carried_item_count(instance: Any, item: str) -> int | None:
    """How much of `item` the agent carries, or None when nothing answered.

    None is not zero. A stage that refused to run because an unread inventory
    looked empty would report a supply failure the world never had, which is
    the same substitution that once turned "not executed" into "measured
    zero" and cost eleven generations of misdirected diagnosis.
    """
    command = (
        "/c "
        "local p=storage.agent_characters and storage.agent_characters[1]; "
        "if not p then rcon.print('') return end; "
        "local inv=p.get_main_inventory(); "
        "if not inv then rcon.print('') return end; "
        f"rcon.print(inv.get_item_count('{item}'))"
    )
    try:
        raw = instance.rcon_client.send_command(command)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _supply_log_rows(raw: Any) -> list[dict[str, Any]] | None:
    """Per-container draw rows for the journal, or None when nothing was read.

    The remote script appends one triple per container it tried. A missing
    attribute means the step never reached the draw, and that is reported as
    None: an empty list would read as a step that drew from nowhere, which is
    precisely the question the ledger exists to answer. A log whose shape does
    not match is discarded whole, because a partially parsed list reads as a
    complete census of what was taken.
    """
    if not isinstance(raw, (list, tuple)):
        return None
    rows: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            return None
        x, y, quantity = entry
        try:
            rows.append(
                {
                    "position": {"x": float(x), "y": float(y)},
                    "quantity": float(quantity),
                }
            )
        except (TypeError, ValueError):
            return None
    return rows


def _runtime_entity_footprints(instance: Any) -> dict[str, tuple[int, int]]:
    """Tile footprints for every placeable entity, straight from the runtime.

    Reuses the dashboard prototype command over the RCON client this stage
    already holds, so the Lua that reads ``prototypes.entity`` is stated once
    in the codebase. Best effort by design: any RCON or payload failure
    returns an empty map and the caller falls back to the ``tile_dimensions``
    the entity snapshot carries, then to the static table in
    ``factorio_ai_lab.planning.footprints``.
    """
    try:
        from factorio_ai_lab.dashboard.state import FactorioObserver

        raw = instance.rcon_client.send_command(
            FactorioObserver._ENTITY_PROTOTYPE_COMMAND
        )
        if not raw:
            return {}
        return prototype_footprints(json.loads(raw))
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return {}


def _runtime_crafting_speed(instance: Any, machine: str) -> float | None:
    """Crafting speed of ``machine`` as the live prototypes report it.

    Reuses the dashboard knowledge command over the RCON client this stage
    already holds. Returns None when the runtime does not answer, or answers
    that it has no measured speed for that machine. Callers must record that
    absence instead of substituting a literal: a speed read off the wiki by
    eye cannot size one machine against another.
    """
    try:
        from factorio_ai_lab.dashboard.state import FactorioObserver
        from factorio_ai_lab.planning.runtime_catalog import (
            RuntimeFactorioCatalog,
        )

        raw = instance.rcon_client.send_command(
            FactorioObserver._GAME_KNOWLEDGE_COMMAND
        )
        if not raw:
            return None
        reading = RuntimeFactorioCatalog(json.loads(raw)).machine_speed(machine)
        if not reading.crafting_speed_measured:
            return None
        speed = reading.crafting_speed
        return speed if speed is not None and speed > 0 else None
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None



def _step_error_text(info: dict[str, Any] | None) -> str | None:
    """Human-readable failure text for a FLE step.

    The gym environment returns ``{"error_occurred": bool, "result": str, ...}``
    (fle/env/gym_env/environment.py:504). There is no ``"error"`` key, so
    reading one always yielded None and every counterexample was recorded with
    ``"error": null`` -- which is why a stage that aborted mid-script looked
    like a stage that measured zero. The message lives in ``result``.
    """
    if not info or not info.get("error_occurred"):
        return None
    result = info.get("result")
    if result is None:
        return None
    text = str(result).strip()
    return text[:1200] if text else None

def stage_astar_logistics(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    center: tuple[float, float],
    settle_seconds: int,
    turn_penalty: float,
    region: tuple[float, float, float, float] | None = None,
) -> dict[str, Any] | None:
    namespace = env.unwrapped.instance.namespace
    instance = env.unwrapped.instance

    anchor = (center[0] - 8.0, center[1] - 0.5)
    # One reading of the world decides the drill, the route and the terminal,
    # so the belt is planned around the same world the drill was placed
    # against. The anchor is a fixed offset from a deterministic patch
    # centre, which is how an heir came to aim its drill at the tiles the
    # inherited cell at (19, 83) already stands on.
    survey = survey_world(env)
    drill_plan = plan_cell_placement(
        survey,
        anchor,
        entity=BASELINE_DRILL_NAME,
        adopt_names=TRIAL_ADOPTS_NOTHING,
        reach=PLACEMENT_SCAN_REACH,
        region=region,
        extra_tiles=logistics_cell_reserve(
            None if survey is None else survey.footprints
        ),
    )
    journal.state["metrics"]["logistics_placement"] = drill_plan.to_dict()
    if drill_plan.position is None:
        journal.fail_stage(
            4,
            f"A* logistics found no free tiles for its cell: {drill_plan.reason}.",
        )
        journal.event(
            "refusal",
            "A* logistics refused: the standing factory occupies its tiles.",
            placement=drill_plan.to_dict(),
        )
        return None
    drill_position = drill_plan.position
    geometry = logistics_geometry(drill_position)
    start = geometry.start
    goal = geometry.goal

    patch_bounds = journal.state.get("world", {}).get("patch_bounds", {})
    left_top = patch_bounds.get("left_top", {})
    right_bottom = patch_bounds.get("right_bottom", {})
    min_x = math.floor(float(left_top.get("x", center[0] - 14)))
    max_x = math.ceil(float(right_bottom.get("x", center[0] + 14)))
    min_y = math.floor(float(left_top.get("y", center[1] - 14)))
    max_y = math.ceil(float(right_bottom.get("y", center[1] + 14)))

    if survey is not None:
        entities: list[Any] = [dict(entity) for entity in survey.entities]
        runtime_footprints = dict(survey.footprints)
    else:
        entities = namespace._save_entity_state(
            distance=500,
            player_entities=True,
            resource_entities=False,
            items_on_ground=False,
            encode=False,
            compress=False,
        )
        runtime_footprints = _runtime_entity_footprints(instance)
    # Block the tiles each entity really occupies. A guessed radius both
    # over-blocks 2x2 entities and leaves 3x3 machines open, which is how a
    # planned belt ends up crossing a machine and only failing at place_entity.
    blocked: set[GridPoint] = blocked_tiles(entities, runtime_footprints)

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

    astar_route = route
    planner_name = "weighted_astar"
    neural_metadata_path = RUNS_DIR / "models" / "spatial_policy.json"
    neural_model_path = RUNS_DIR / "models" / "spatial_policy.npz"
    neural_candidate: RouteResult | None = None
    if neural_metadata_path.exists() and neural_model_path.exists():
        try:
            metadata = json.loads(neural_metadata_path.read_text(encoding="utf-8"))
            if isinstance(metadata, dict) and metadata.get("usable"):
                policy = SpatialPolicy.load(neural_model_path)
                neural_path = policy.rollout(
                    start,
                    goal,
                    is_blocked=blocked.__contains__,
                    in_bounds=lambda point: (
                        min_x <= point.x <= max_x
                        and min_y <= point.y <= max_y
                    ),
                    max_steps=512,
                )
                if neural_path is not None:
                    neural_cost = route_cost(
                        neural_path,
                        turn_penalty=turn_penalty,
                    )
                    neural_candidate = RouteResult(
                        path=neural_path,
                        cost=neural_cost,
                        expanded_nodes=len(neural_path),
                    )
                    if neural_cost <= astar_route.cost * 1.10:
                        route = neural_candidate
                        planner_name = "neural_spatial_policy"
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            neural_candidate = None

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

    # The same geometry the placement reserved tiles for, not a second copy
    # of the offsets: a terminal computed here would drift from the tiles the
    # drill was moved to keep free.
    inserter_position = geometry.inserter
    chest_position = geometry.chest

    # The cell is charged and its container found in the standing world. The
    # heir of generation 37 carries 44 drills, 492 belts and 41 inserters,
    # and not one chest: measured off the promoted checkpoint's inheritance
    # ledger on 2026-09-23.
    drill_dose = max(
        SCALE_COAL_FLOOR,
        BURNER_MINING_DRILL.coal_for_seconds(
            float(settle_seconds) + STAGE_OVERHEAD_SECONDS
        ),
    )
    logistics_dose = drill_dose + LOGISTICS_INSERTER_COAL
    supply = survey_stage_supply(
        env,
        anchor=drill_position,
        fuel_needed=logistics_dose,
        container_needed=True,
    )
    journal.state["metrics"]["logistics_supply"] = supply_report(supply)
    journal.state["metrics"]["logistics_coal_dose"] = logistics_dose
    withheld_supply = blocking_refusals(supply)
    if withheld_supply:
        journal.fail_stage(
            4,
            "A* logistics refused: the standing world supplies no terminal "
            f"({', '.join(withheld_supply)}).",
        )
        journal.event(
            "refusal",
            "A* logistics refused: neither fuel nor a container in the world.",
            supply=journal.state["metrics"]["logistics_supply"],
        )
        return None
    supply_prelude = mining_cell_supply_script(supply, fuel_needed=logistics_dose)
    logistics_container = cell_container(supply)
    logistics_container_binding = (
        "logistics_container_type = Prototype."
        f"{container_prototype(logistics_container)}"
    )
    journal.state["metrics"]["logistics_container"] = logistics_container

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
        "Routing planner generated a persistent belt-route challenger.",
        planner=planner_name,
        footprint_source=(
            "runtime_prototypes" if runtime_footprints else "entity_snapshot_or_static"
        ),
        blocked_tile_count=len(blocked),
        astar_cost=astar_route.cost,
        neural_cost=neural_candidate.cost if neural_candidate is not None else None,
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
            container=logistics_container,
        )
        measured["chest_iron"] = float(chest_iron)
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and chest_iron > 0
        )

    code = f"""
{logistics_container_binding}
logistics_drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={drill_position[0]},y={drill_position[1]}),
    direction=Direction.DOWN,
)
{supply_prelude}
logistics_drill_fuel=min({drill_dose},inspect_inventory()[Prototype.Coal])
if logistics_drill_fuel>0:
    logistics_drill=insert_item(
        Prototype.Coal,
        logistics_drill,
        quantity=logistics_drill_fuel,
    )
{chr(10).join(belt_lines)}
logistics_inserter=place_entity(
    Prototype.BurnerInserter,
    position=Position(x={inserter_position[0]},y={inserter_position[1]}),
    direction=Direction.RIGHT,
)
logistics_inserter_fuel=min(
    {LOGISTICS_INSERTER_COAL},
    inspect_inventory()[Prototype.Coal],
)
if logistics_inserter_fuel>0:
    logistics_inserter=insert_item(
        Prototype.Coal,
        logistics_inserter,
        quantity=logistics_inserter_fuel,
    )
logistics_chest=place_entity(
    logistics_container_type,
    position=Position(x={chest_position[0]},y={chest_position[1]}),
    direction=Direction.UP,
)
sleep({settle_seconds})
print({{
    'logistics_chest': inspect_inventory(logistics_chest),
    'logistics_drill_fuel': logistics_drill_fuel,
    'logistics_inserter_fuel': logistics_inserter_fuel,
    'supply_fuel_drawn': supply_fuel_drawn,
}})
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
            # None, never 0.0, when the step never reached the draw: a
            # withdrawal nobody measured is not a withdrawal of nothing.
            "logistics_supply_drawn": {
                "drill_coal_inserted": _namespace_measure(
                    namespace,
                    "logistics_drill_fuel",
                ),
                "inserter_coal_inserted": _namespace_measure(
                    namespace,
                    "logistics_inserter_fuel",
                ),
                "supply_fuel_drawn": _namespace_measure(
                    namespace,
                    "supply_fuel_drawn",
                ),
                "supply_container_recovered": _namespace_measure(
                    namespace,
                    "supply_container_recovered",
                ),
                "supply_container_smelted": _namespace_measure(
                    namespace,
                    "supply_container_smelted",
                ),
                "supply_draws": _supply_log_rows(
                    getattr(namespace, "supply_fuel_log", None)
                ),
            },
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
            "planner": planner_name,
            "teacher_planner": "weighted_astar",
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
            "neural_candidate_available": neural_candidate is not None,
        },
    )
    lesson = synthesize_lesson(
        stage="astar_belt_logistics",
        facts={
            "accepted": True,
            "planner": planner_name,
            "astar_cost": astar_route.cost,
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
        "planner": planner_name,
        "astar_cost": astar_route.cost,
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
        # Which container the terminal actually is. The next stage opens it
        # by prototype, and a stage that assumed the baseline chest would
        # read an entity that is not there.
        "chest_name": logistics_container,
        "drill_position": {
            "x": drill_position[0],
            "y": drill_position[1],
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
    chest_name = str(logistics.get("chest_name", BASELINE_CHEST_NAME))
    downstream_inserter = {
        "x": float(chest["x"]) + 1.0,
        "y": float(chest["y"]),
    }

    # Both burners are charged out of the standing world, for the same reason
    # the stages before this one are: the heir carries no coal by the time it
    # gets here. The tiles these two go on were reserved by the placement of
    # stage 4, which is why this stage plans no position of its own.
    furnace_dose = max(
        SMELT_COAL_FLOOR,
        STONE_FURNACE.coal_for_seconds(
            float(settle_seconds) + STAGE_OVERHEAD_SECONDS
        ),
    )
    belt_dose = furnace_dose + LOGISTICS_INSERTER_COAL
    supply = survey_stage_supply(
        env,
        anchor=(float(chest["x"]), float(chest["y"])),
        fuel_needed=belt_dose,
        container_needed=False,
    )
    journal.state["metrics"]["belt_smelting_supply"] = supply_report(supply)
    journal.state["metrics"]["belt_smelting_coal_dose"] = belt_dose
    withheld_supply = blocking_refusals(supply)
    if withheld_supply:
        journal.fail_stage(
            5,
            "Belt-fed smelting refused: the standing world holds no fuel for "
            f"it ({', '.join(withheld_supply)}).",
        )
        journal.event(
            "refusal",
            "Belt-fed smelting refused: no fuel in the standing world.",
            supply=journal.state["metrics"]["belt_smelting_supply"],
        )
        return False
    supply_prelude = mining_cell_supply_script(supply, fuel_needed=belt_dose)
    belt_container_binding = (
        f"belt_container_type = Prototype.{container_prototype(chest_name)}"
    )

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
    clock = _StageClock(env)
    measured: dict[str, Any] = {
        "iron_plate_world_before": plate_before,
    }

    def validate_belt_smelting(result: Any) -> bool:
        clock.stop()
        plate_after = production_output(namespace, "iron-plate")
        measured["iron_plate_world_after"] = plate_after
        # The world counter over this window also carries the probe furnace
        # stage 3 left burning and every furnace the inherited factory runs,
        # so it is recorded as world flow and gates nothing.
        measured["iron_plate_world_flow"] = max(0.0, plate_after - plate_before)
        measured["belt_plate_output"] = _namespace_measure(
            namespace,
            "belt_plates",
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and _measured_above(measured, "belt_plate_output")
        )

    fast_reposition(
        env,
        x=downstream_inserter["x"],
        y=downstream_inserter["y"],
    )
    code = f"""
{belt_container_binding}
buffer_chest=get_entity(
    belt_container_type,
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
{supply_prelude}
belt_inserter_fuel=min(
    {LOGISTICS_INSERTER_COAL},
    inspect_inventory()[Prototype.Coal],
)
if belt_inserter_fuel>0:
    smelt_out_inserter=insert_item(
        Prototype.Coal,
        smelt_out_inserter,
        quantity=belt_inserter_fuel,
    )
belt_furnace=place_entity_next_to(
    Prototype.StoneFurnace,
    smelt_out_inserter.position,
    direction=Direction.RIGHT,
)
belt_furnace_fuel=min({furnace_dose},inspect_inventory()[Prototype.Coal])
if belt_furnace_fuel>0:
    belt_furnace=insert_item(
        Prototype.Coal,
        belt_furnace,
        quantity=belt_furnace_fuel,
    )
belt_plates_before=inspect_inventory(belt_furnace)[Prototype.IronPlate]
sleep({settle_seconds})
belt_plates=max(
    0,
    inspect_inventory(belt_furnace)[Prototype.IronPlate]-belt_plates_before,
)
print({{
    'buffer': inspect_inventory(buffer_chest),
    'furnace': inspect_inventory(belt_furnace),
    'belt_plates': belt_plates,
    'belt_inserter_fuel': belt_inserter_fuel,
    'belt_furnace_fuel': belt_furnace_fuel,
    'supply_fuel_drawn': supply_fuel_drawn,
}})
"""
    step = executor.execute(
        code,
        accept=validate_belt_smelting,
        use_checkpoint_for_action=False,
    )

    journal.state["metrics"]["belt_smelting_plate_world_flow"] = measured.get(
        "iron_plate_world_flow"
    )
    journal.state["metrics"]["belt_smelting_plate_basis"] = (
        BELT_SMELTING_OUTPUT_BASIS
    )
    if not step.accepted:
        journal.fail_stage(
            5,
            "Buffered belt-to-furnace integration produced no validated plates; rolled back.",
        )
        journal.event(
            "reject",
            "Belt-fed smelting integration rejected and rolled back.",
            # None, never 0.0: a step that aborted before the furnace was read
            # did not measure an empty furnace.
            iron_plate_output=measured.get("belt_plate_output"),
            iron_plate_world_flow=measured.get("iron_plate_world_flow"),
        )
        return False

    plates = float(measured["belt_plate_output"])
    # The direct-feed baseline is a reading stage 3 either took or did not,
    # and ``metrics.get(key, 0.0)`` made the second case look like the first:
    # a baseline window of 0.0 seconds reaches rate_per_second, which raises
    # "duration_s must be positive" from inside a stage whose own window was
    # measured perfectly well. Absent stays absent, and a comparison with no
    # baseline is reported as no comparison.
    direct = _recorded_metric(journal, "iron_plate_output")
    direct_duration = _recorded_metric(journal, "direct_smelting_duration_s")
    direct_rate = _recorded_metric(journal, "direct_smelting_plate_rate_per_s")
    if direct_rate is None and direct is not None and direct_duration:
        direct_rate = direct / direct_duration
    belt_count = max(1, int(logistics["belt_count"]))
    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="belt_smelting",
        fallback_seconds=float(settle_seconds),
    )
    belt_rate = rate_per_second(plates, window)
    ratio = (
        None
        if direct is None or not direct_duration
        else normalized_rate_ratio(
            candidate_count=plates,
            candidate_duration_s=window,
            baseline_count=direct,
            baseline_duration_s=direct_duration,
        )
    )
    plate_rate_per_belt = belt_rate / belt_count

    journal.state["metrics"].update(
        {
            "belt_smelting_plate_output": plates,
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
            f"over {window:.0f}s ({belt_rate:.3f} plates/s); "
            f"{ratio:.3f}x the normalized direct-feed rate." if ratio is not None else "no valid direct-feed rate baseline."
        ),
    )
    journal.event(
        "accept",
        "Buffered belt-fed smelting accepted.",
        iron_plate_output=plates,
        iron_plate_world_flow=measured.get("iron_plate_world_flow"),
        output_basis=BELT_SMELTING_OUTPUT_BASIS,
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
            "belt_smelting_plate_basis": BELT_SMELTING_OUTPUT_BASIS,
            "belt_smelting_duration_s": window,
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


def stage_electronic_circuits(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    journal.set_stage(
        13,
        status="running",
        detail=(
            "Producing copper cable and electronic circuits in electrically "
            "powered assemblers."
        ),
        next_action="validate electronic-circuit manufacturing chain",
    )
    before = production_output(namespace, "electronic-circuit")
    clock = _StageClock(env)
    measured: dict[str, Any] = {}

    code = f"""
# Reactivate upstream production first. This stage must prove a causal
# coal -> copper ore -> copper plate -> cable -> circuit chain rather than
# consuming leftovers from red-science validation.
move_to(coal_chest.position)
circuit_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
circuit_coal=0
if circuit_coal_available>0:
    circuit_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(10,circuit_coal_available),
    )
if circuit_coal>=1:
    coal_drill=insert_item(Prototype.Coal,coal_drill,quantity=1)
if circuit_coal>=2:
    copper_drill=insert_item(Prototype.Coal,copper_drill,quantity=1)
if circuit_coal>=3:
    drill=insert_item(Prototype.Coal,drill,quantity=1)
if circuit_coal>=4:
    scale_drill=insert_item(Prototype.Coal,scale_drill,quantity=1)
sleep(14)

circuit_nav_note=''
circuit_copper_buffer_before=0
circuit_copper_ore=0
circuit_iron_ore=0
try:
    move_to(copper_chest.position)
except Exception as circuit_exc:
    circuit_nav_note='copper_chest: '+str(circuit_exc)[:160].replace('rror','rr0r').replace('xception','xcepti0n')
circuit_copper_buffer_before=inspect_inventory(copper_chest)[Prototype.CopperOre]
if circuit_copper_buffer_before>0:
    circuit_copper_ore=extract_item(
        Prototype.CopperOre,
        copper_chest,
        quantity=min(24,circuit_copper_buffer_before),
    )
if chest is None:
    for circuit_candidate in get_entities(Prototype.WoodenChest):
        if inspect_inventory(circuit_candidate)[Prototype.IronOre]>0:
            chest=circuit_candidate
            break
    circuit_nav_note=(circuit_nav_note+' | ' if circuit_nav_note else '')+('iron_chest recovered by scan' if chest is not None else 'iron_chest missing and no chest holds iron ore')
if chest is not None:
    try:
        move_to(chest.position)
    except Exception as circuit_exc:
        circuit_nav_note=(circuit_nav_note+' | ' if circuit_nav_note else '')+'iron_chest move: '+str(circuit_exc)[:160].replace('rror','rr0r').replace('xception','xcepti0n')
    circuit_iron_ore=inspect_inventory(chest)[Prototype.IronOre]
    if circuit_iron_ore>0:
        circuit_iron_ore=extract_item(
            Prototype.IronOre,
            chest,
            quantity=min(24,circuit_iron_ore),
        )

move_to(copper_furnace.position)
if circuit_coal>=5:
    copper_furnace=insert_item(
        Prototype.Coal,
        copper_furnace,
        quantity=2,
    )
if circuit_copper_ore>0:
    circuit_cu_in_furnace=inspect_inventory(copper_furnace)[Prototype.CopperOre]
    circuit_cu_room=max(0,24-circuit_cu_in_furnace)
    circuit_copper_ore=min(circuit_copper_ore,circuit_cu_room)
    if circuit_copper_ore>0:
        copper_furnace=insert_item(
            Prototype.CopperOre,
            copper_furnace,
            quantity=circuit_copper_ore,
        )
move_to(smelt_furnace.position)
if circuit_coal>=7:
    smelt_furnace=insert_item(
        Prototype.Coal,
        smelt_furnace,
        quantity=2,
    )
if circuit_iron_ore>0:
    circuit_furnace_ore=inspect_inventory(smelt_furnace)[Prototype.IronOre]
    circuit_ore_room=max(0,24-circuit_furnace_ore)
    circuit_iron_ore=min(circuit_iron_ore,circuit_ore_room)
    if circuit_iron_ore>0:
        smelt_furnace=insert_item(
            Prototype.IronOre,
            smelt_furnace,
            quantity=circuit_iron_ore,
        )
sleep(20)

move_to(copper_furnace.position)
circuit_copper=inspect_inventory(copper_furnace)[Prototype.CopperPlate]
if circuit_copper>0:
    circuit_copper=extract_item(
        Prototype.CopperPlate,
        copper_furnace,
        quantity=min(24,circuit_copper),
    )
move_to(smelt_furnace.position)
circuit_iron=inspect_inventory(smelt_furnace)[Prototype.IronPlate]
if circuit_iron>0:
    circuit_iron=extract_item(
        Prototype.IronPlate,
        smelt_furnace,
        quantity=min(24,circuit_iron),
    )

cable_area=nearest_buildable(
    Prototype.AssemblingMachine2,
    BuildingBox(width=9,height=9),
    science_assembler.position,
)
move_to(cable_area.center)
cable_assembler=place_entity(
    Prototype.AssemblingMachine2,
    position=cable_area.center,
)
cable_assembler=set_entity_recipe(cable_assembler,Prototype.CopperCable)
if circuit_copper>0:
    cable_assembler=insert_item(
        Prototype.CopperPlate,
        cable_assembler,
        quantity=circuit_copper,
    )
cable_power=connect_entities(
    steam_engine,
    cable_assembler,
    Prototype.MediumElectricPole,
)
sleep({max(6, settle_seconds // 2)})
cable_inventory=inspect_inventory(cable_assembler)[Prototype.CopperCable]
cable_transfer=0
if cable_inventory>0:
    cable_transfer=extract_item(
        Prototype.CopperCable,
        cable_assembler,
        quantity=cable_inventory,
    )

circuit_area=nearest_buildable(
    Prototype.AssemblingMachine2,
    BuildingBox(width=9,height=9),
    cable_assembler.position,
)
move_to(circuit_area.center)
circuit_assembler=place_entity(
    Prototype.AssemblingMachine2,
    position=circuit_area.center,
)
circuit_assembler=set_entity_recipe(
    circuit_assembler,
    Prototype.ElectronicCircuit,
)
if cable_transfer>0:
    circuit_assembler=insert_item(
        Prototype.CopperCable,
        circuit_assembler,
        quantity=cable_transfer,
    )
if circuit_iron>0:
    circuit_assembler=insert_item(
        Prototype.IronPlate,
        circuit_assembler,
        quantity=circuit_iron,
    )
circuit_power=connect_entities(
    steam_engine,
    circuit_assembler,
    Prototype.MediumElectricPole,
)
sleep({settle_seconds})
circuit_inventory=inspect_inventory(
    circuit_assembler,
)[Prototype.ElectronicCircuit]
print({{
    'circuit_coal_available':circuit_coal_available,
    'circuit_coal':circuit_coal,
    'circuit_copper_buffer_before':circuit_copper_buffer_before,
    'circuit_nav_note':circuit_nav_note,
    'circuit_copper_ore':circuit_copper_ore,
    'circuit_iron_ore':circuit_iron_ore,
    'circuit_copper':circuit_copper,
    'circuit_iron':circuit_iron,
    'cable_transfer':cable_transfer,
    'circuit_inventory':circuit_inventory,
}})
"""

    def validate(result: Any) -> bool:
        clock.stop()
        output = max(
            0.0,
            production_output(namespace, "electronic-circuit") - before,
        )
        measured["output"] = output
        measured["inventory"] = float(
            getattr(namespace, "circuit_inventory", 0.0) or 0.0
        )
        measured["cable"] = float(
            getattr(namespace, "cable_transfer", 0.0) or 0.0
        )
        # A missing attribute means the script aborted before assigning it.
        # Defaulting that to 0.0 made an abort indistinguishable from a real
        # measurement of zero, which is how an aborted stage was read for
        # eleven generations as "the iron buffer was empty" while the buffer
        # actually held ~137 ore.
        for key in (
            "circuit_coal_available",
            "circuit_coal",
            "circuit_copper_buffer_before",
            "circuit_copper_ore",
            "circuit_iron_ore",
            "circuit_copper",
            "circuit_iron",
        ):
            measured[key] = _namespace_measure(namespace, key)
        nav_error = getattr(namespace, "circuit_nav_note", "") or ""
        measured["circuit_nav_note"] = str(nav_error)[:400] or None
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and measured["cable"] > 0
            and (output > 0 or measured["inventory"] > 0)
        )

    step = executor.execute(
        code,
        accept=validate,
        use_checkpoint_for_action=False,
    )
    output = max(measured.get("output", 0.0), measured.get("inventory", 0.0))
    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="electronic_circuit",
        fallback_seconds=float(settle_seconds),
    )
    journal.state["metrics"]["electronic_circuit_output"] = output
    journal.state["metrics"]["electronic_circuit_rate_per_s"] = rate_per_second(
        output,
        window,
    )
    if not step.accepted:
        journal.state["metrics"]["electronic_circuit_counterexample"] = {
            **measured,
            "error_occurred": bool(step.info.get("error_occurred")),
            "error": _step_error_text(step.info),
        }
        journal.fail_stage(
            13,
            "Electronic-circuit causal chain produced no validated circuits.",
        )
        journal.event(
            "counterexample",
            "Electronic-circuit DAG rejected with causal buffer measurements.",
            measurements=measured,
            error=_step_error_text(step.info),
        )
        return False

    journal.complete_stage(
        13,
        f"Electronic-circuit chain accepted with {output:.0f} circuits.",
    )
    journal.event(
        "accept",
        "Powered copper-cable and electronic-circuit assemblers accepted.",
        measurements=measured,
    )
    return True


def stage_logistic_science(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    settle_seconds: int,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    target_rate = 0.10
    dag = EARLY_GAME_PRODUCTION_PLANNER.plan(
        "logistic-science-pack",
        target_rate,
    )
    horizon = max(30, settle_seconds)
    safety_factor = 2.0
    iron_plate_budget = max(
        12,
        math.ceil(
            float(dag.raw_requirements_per_s.get("iron-plate", 0.0))
            * horizon
            * safety_factor
        ),
    )
    copper_plate_budget = max(
        8,
        math.ceil(
            float(dag.raw_requirements_per_s.get("copper-plate", 0.0))
            * horizon
            * safety_factor
        ),
    )
    gear_node = dag.node("iron-gear-wheel")
    circuit_node = dag.node("electronic-circuit")
    green_target = max(2, math.ceil(target_rate * settle_seconds))

    journal.state.setdefault("production_plans", {})["logistic_science"] = {
        "factorio_data_version": FACTORIO_DATA_VERSION,
        "target_rate_per_s": target_rate,
        "validation_horizon_s": horizon,
        "safety_factor": safety_factor,
        "dag": dag.to_dict(),
        "material_budget": {
            "iron_plate": iron_plate_budget,
            "copper_plate": copper_plate_budget,
        },
    }
    assembler = "assembling-machine-2"
    assembler_speed = _runtime_crafting_speed(env.unwrapped.instance, assembler)
    journal.event(
        "production_plan",
        "Rate-balanced production DAG generated for logistic science.",
        factorio_data_version=FACTORIO_DATA_VERSION,
        target_rate_per_s=target_rate,
        raw_requirements_per_s=dict(dag.raw_requirements_per_s),
        crafting_machine=assembler,
        crafting_speed=assembler_speed,
        crafting_speed_source=(
            "live_factorio_prototypes"
            if assembler_speed is not None
            else "runtime_speed_unavailable"
        ),
        minimum_machine_count=(
            sum(
                node.minimum_machines(crafting_speed=assembler_speed)
                for node in dag.nodes
            )
            if assembler_speed is not None
            else None
        ),
    )
    journal.set_stage(
        14,
        status="running",
        detail=(
            "Execute the Factorio 2.0.73 rate-balanced green-science DAG using "
            "only validated internal plate/fuel buffers."
        ),
        next_action="validate green-science industrial DAG",
    )
    before = production_output(namespace, "logistic-science-pack")
    clock = _StageClock(env)
    measured: dict[str, float] = {}

    gear_plate_budget = max(
        8,
        math.ceil(
            (gear_node.target_rate_per_s if gear_node else 0.15)
            * 2.0
            * horizon
            * safety_factor
        ),
    )
    circuit_target = max(
        4,
        math.ceil(
            (circuit_node.target_rate_per_s if circuit_node else 0.10)
            * horizon
            * safety_factor
        ),
    )

    code = f"""
# Pull endogenous fuel and raw ore from the validated supply chains.
move_to(coal_chest.position)
green_coal_available=inspect_inventory(coal_chest)[Prototype.Coal]
green_coal=0
if green_coal_available>0:
    green_coal=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min(10,green_coal_available),
    )

move_to(chest.position)
green_iron_ore_available=inspect_inventory(chest)[Prototype.IronOre]
green_iron_ore=0
if green_iron_ore_available>0:
    green_iron_ore=extract_item(
        Prototype.IronOre,
        chest,
        quantity=min({iron_plate_budget * 2},green_iron_ore_available),
    )
if green_iron_ore<{iron_plate_budget}:
    move_to(scale_chest.position)
    scale_iron_available=inspect_inventory(scale_chest)[Prototype.IronOre]
    if scale_iron_available>0:
        green_iron_ore+=extract_item(
            Prototype.IronOre,
            scale_chest,
            quantity=min(
                {iron_plate_budget * 2}-green_iron_ore,
                scale_iron_available,
            ),
        )

move_to(copper_chest.position)
green_copper_ore_available=inspect_inventory(copper_chest)[Prototype.CopperOre]
green_copper_ore=0
if green_copper_ore_available>0:
    green_copper_ore=extract_item(
        Prototype.CopperOre,
        copper_chest,
        quantity=min({copper_plate_budget * 2},green_copper_ore_available),
    )

# Refill the proven furnaces; no benchmark inventory is used for materials.
move_to(smelt_furnace.position)
if green_coal>0:
    smelt_furnace=insert_item(
        Prototype.Coal,
        smelt_furnace,
        quantity=min(4,green_coal),
    )
if green_iron_ore>0:
    smelt_furnace=insert_item(
        Prototype.IronOre,
        smelt_furnace,
        quantity=green_iron_ore,
    )
move_to(copper_furnace.position)
if green_coal>4:
    copper_furnace=insert_item(
        Prototype.Coal,
        copper_furnace,
        quantity=min(4,green_coal-4),
    )
if green_copper_ore>0:
    copper_furnace=insert_item(
        Prototype.CopperOre,
        copper_furnace,
        quantity=green_copper_ore,
    )
sleep(20)

move_to(smelt_furnace.position)
green_iron_available=inspect_inventory(smelt_furnace)[Prototype.IronPlate]
green_iron=0
if green_iron_available>0:
    green_iron=extract_item(
        Prototype.IronPlate,
        smelt_furnace,
        quantity=min({iron_plate_budget},green_iron_available),
    )
move_to(copper_furnace.position)
green_copper_available=inspect_inventory(copper_furnace)[Prototype.CopperPlate]
green_copper=0
if green_copper_available>0:
    green_copper=extract_item(
        Prototype.CopperPlate,
        copper_furnace,
        quantity=min({copper_plate_budget},green_copper_available),
    )

# Replenish the shared gear cell from internally smelted iron.
move_to(gear_assembler.position)
if green_iron>0:
    gear_input=min({gear_plate_budget},green_iron)
    gear_assembler=insert_item(
        Prototype.IronPlate,
        gear_assembler,
        quantity=gear_input,
    )
    green_iron-=gear_input

# Replenish cable/circuit intermediates from internally smelted plates.
move_to(cable_assembler.position)
if green_copper>0:
    cable_assembler=insert_item(
        Prototype.CopperPlate,
        cable_assembler,
        quantity=green_copper,
    )
sleep(10)
green_cables=inspect_inventory(cable_assembler)[Prototype.CopperCable]
if green_cables>0:
    green_cables=extract_item(
        Prototype.CopperCable,
        cable_assembler,
        quantity=green_cables,
    )

move_to(circuit_assembler.position)
if green_cables>0:
    circuit_assembler=insert_item(
        Prototype.CopperCable,
        circuit_assembler,
        quantity=green_cables,
    )
if green_iron>0:
    circuit_iron_input=min({circuit_target},green_iron)
    circuit_assembler=insert_item(
        Prototype.IronPlate,
        circuit_assembler,
        quantity=circuit_iron_input,
    )
    green_iron-=circuit_iron_input
sleep(10)

green_gears=inspect_inventory(gear_assembler)[Prototype.IronGearWheel]
if green_gears>0:
    green_gears=extract_item(
        Prototype.IronGearWheel,
        gear_assembler,
        quantity=green_gears,
    )
green_circuits=inspect_inventory(
    circuit_assembler,
)[Prototype.ElectronicCircuit]
if green_circuits>0:
    green_circuits=extract_item(
        Prototype.ElectronicCircuit,
        circuit_assembler,
        quantity=green_circuits,
    )

belt_area=nearest_buildable(
    Prototype.AssemblingMachine2,
    BuildingBox(width=9,height=9),
    circuit_assembler.position,
)
move_to(belt_area.center)
belt_assembler=place_entity(
    Prototype.AssemblingMachine2,
    position=belt_area.center,
)
belt_assembler=set_entity_recipe(
    belt_assembler,
    Prototype.TransportBelt,
)
if green_gears>0:
    belt_gear_input=min(max(2,{green_target}),green_gears)
    belt_assembler=insert_item(
        Prototype.IronGearWheel,
        belt_assembler,
        quantity=belt_gear_input,
    )
    green_gears-=belt_gear_input
if green_iron>0:
    belt_iron_input=min(max(2,{green_target}),green_iron)
    belt_assembler=insert_item(
        Prototype.IronPlate,
        belt_assembler,
        quantity=belt_iron_input,
    )
    green_iron-=belt_iron_input
belt_power=connect_entities(
    steam_engine,
    belt_assembler,
    Prototype.MediumElectricPole,
)

inserter_area=nearest_buildable(
    Prototype.AssemblingMachine2,
    BuildingBox(width=9,height=9),
    belt_assembler.position,
)
move_to(inserter_area.center)
inserter_assembler=place_entity(
    Prototype.AssemblingMachine2,
    position=inserter_area.center,
)
inserter_assembler=set_entity_recipe(
    inserter_assembler,
    Prototype.Inserter,
)
if green_gears>0:
    inserter_gear_input=min(max(2,{green_target}),green_gears)
    inserter_assembler=insert_item(
        Prototype.IronGearWheel,
        inserter_assembler,
        quantity=inserter_gear_input,
    )
if green_iron>0:
    inserter_iron_input=min(max(2,{green_target}),green_iron)
    inserter_assembler=insert_item(
        Prototype.IronPlate,
        inserter_assembler,
        quantity=inserter_iron_input,
    )
if green_circuits>0:
    inserter_circuit_input=min(max(2,{green_target}),green_circuits)
    inserter_assembler=insert_item(
        Prototype.ElectronicCircuit,
        inserter_assembler,
        quantity=inserter_circuit_input,
    )
inserter_power=connect_entities(
    steam_engine,
    inserter_assembler,
    Prototype.MediumElectricPole,
)
sleep(10)

green_belts=inspect_inventory(belt_assembler)[Prototype.TransportBelt]
if green_belts>0:
    green_belts=extract_item(
        Prototype.TransportBelt,
        belt_assembler,
        quantity=green_belts,
    )
green_inserters=inspect_inventory(inserter_assembler)[Prototype.Inserter]
if green_inserters>0:
    green_inserters=extract_item(
        Prototype.Inserter,
        inserter_assembler,
        quantity=green_inserters,
    )

green_area=nearest_buildable(
    Prototype.AssemblingMachine2,
    BuildingBox(width=9,height=9),
    inserter_assembler.position,
)
move_to(green_area.center)
green_assembler=place_entity(
    Prototype.AssemblingMachine2,
    position=green_area.center,
)
green_assembler=set_entity_recipe(
    green_assembler,
    Prototype.LogisticsSciencePack,
)
if green_belts>0:
    green_assembler=insert_item(
        Prototype.TransportBelt,
        green_assembler,
        quantity=green_belts,
    )
if green_inserters>0:
    green_assembler=insert_item(
        Prototype.Inserter,
        green_assembler,
        quantity=green_inserters,
    )
green_power=connect_entities(
    steam_engine,
    green_assembler,
    Prototype.MediumElectricPole,
)
sleep({settle_seconds})
green_inventory=inspect_inventory(
    green_assembler,
)[Prototype.LogisticsSciencePack]
print({{
    'green_iron_ore':green_iron_ore,
    'green_copper_ore':green_copper_ore,
    'green_gears':green_gears,
    'green_circuits':green_circuits,
    'green_belts':green_belts,
    'green_inserters':green_inserters,
    'green_inventory':green_inventory,
}})
"""

    def validate(result: Any) -> bool:
        clock.stop()
        output = max(
            0.0,
            production_output(namespace, "logistic-science-pack") - before,
        )
        measured["output"] = output
        measured["inventory"] = float(
            getattr(namespace, "green_inventory", 0.0) or 0.0
        )
        measured["belts"] = float(
            getattr(namespace, "green_belts", 0.0) or 0.0
        )
        measured["inserters"] = float(
            getattr(namespace, "green_inserters", 0.0) or 0.0
        )
        measured["iron_ore"] = float(
            getattr(namespace, "green_iron_ore", 0.0) or 0.0
        )
        measured["copper_ore"] = float(
            getattr(namespace, "green_copper_ore", 0.0) or 0.0
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and measured["iron_ore"] > 0
            and measured["copper_ore"] > 0
            and measured["belts"] > 0
            and measured["inserters"] > 0
            and (output > 0 or measured["inventory"] > 0)
        )

    step = executor.execute(
        code,
        accept=validate,
        use_checkpoint_for_action=False,
    )
    output = max(measured.get("output", 0.0), measured.get("inventory", 0.0))
    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="logistic_science",
        fallback_seconds=float(settle_seconds),
    )
    journal.state["metrics"]["logistic_science_output"] = output
    journal.state["metrics"]["logistic_science_rate_per_s"] = rate_per_second(
        output,
        window,
    )
    journal.state["metrics"]["logistic_science_dag_target_rate_per_s"] = target_rate
    if not step.accepted:
        journal.fail_stage(
            14,
            "Internal industrial DAG produced no validated logistic science.",
        )
        journal.event(
            "reject",
            "Green-science DAG rejected; internal material chain was insufficient.",
            measurements=measured,
            production_plan=dag.to_dict(),
        )
        return False

    journal.complete_stage(
        14,
        f"Internal logistic-science DAG accepted with {output:.0f} packs.",
    )
    journal.event(
        "accept",
        "Rate-balanced belts/inserters chain fed powered logistic science.",
        measurements=measured,
        production_plan=dag.to_dict(),
    )
    return True


def stage_transactional_rebuild(
    executor: TransactionalFLEExecutor,
    env: Any,
    journal: ResearchJournal,
    *,
    logistics: dict[str, Any] | None,
    settle_seconds: int,
    gain_threshold: float,
) -> bool:
    namespace = env.unwrapped.instance.namespace
    belt_rate = float(
        journal.state["metrics"].get("belt_smelting_plate_rate_per_s", 0.0)
        or 0.0
    )
    direct_rate = float(
        journal.state["metrics"].get("direct_smelting_plate_rate_per_s", 0.0)
        or 0.0
    )
    ratio = (
        belt_rate / direct_rate
        if direct_rate > 0
        else None
    )

    journal.set_stage(
        15,
        status="validating",
        detail=(
            "Testing whether a dominated belt/smelting branch can be removed "
            "without sacrificing the validated compact iron capability."
        ),
        next_action="transactionally demolish dominated branch or retain it",
    )

    if (
        logistics is None
        or ratio is None
        or ratio >= 1.0 - gain_threshold
    ):
        journal.state["metrics"]["rebuild_attempted"] = False
        journal.state["metrics"]["rebuild_committed"] = False
        journal.complete_stage(
            15,
            "No demolition attempted: the logistics branch is not sufficiently dominated.",
        )
        return True

    pickup_lines = []
    for point in logistics.get("path", []):
        pickup_lines.append(
            "if pickup_entity("
            "Prototype.TransportBelt,"
            f"Position(x={float(point['x'])},y={float(point['y'])})"
            "): removed_count+=1"
        )

    plate_before = production_output(namespace, "iron-plate")
    clock = _StageClock(env)
    measured: dict[str, float] = {}

    code = f"""
removed_count=0
{chr(10).join(pickup_lines)}
for entity in (
    logistics_inserter,
    logistics_chest,
    smelt_out_inserter,
    belt_furnace,
    logistics_drill,
):
    try:
        if pickup_entity(entity):
            removed_count+=1
    except Exception:
        pass

smelt_drill=insert_item(Prototype.Coal,smelt_drill,quantity=6)
smelt_furnace=insert_item(Prototype.Coal,smelt_furnace,quantity=6)
sleep({settle_seconds})
print({{
    'removed_count':removed_count,
    'direct_furnace':inspect_inventory(smelt_furnace),
}})
"""

    def validate(result: Any) -> bool:
        clock.stop()
        plate_after = production_output(namespace, "iron-plate")
        output = max(0.0, plate_after - plate_before)
        candidate_rate = rate_per_second(
            output,
            clock.window(float(settle_seconds)),
        )
        removed = float(getattr(namespace, "removed_count", 0) or 0)
        measured.update(
            {
                "plate_output": output,
                "plate_rate_per_s": candidate_rate,
                "removed_entities": removed,
                "rate_retention": (
                    candidate_rate / direct_rate
                    if direct_rate > 0
                    else 0.0
                ),
            }
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and removed >= float(logistics.get("belt_count", 0))
            and candidate_rate >= direct_rate * (1.0 - gain_threshold)
        )

    step = executor.execute(
        code,
        accept=validate,
        use_checkpoint_for_action=False,
    )
    _record_observed_window(
        journal,
        clock,
        metric_prefix="rebuild",
        fallback_seconds=float(settle_seconds),
    )
    journal.state["metrics"].update(
        {
            "rebuild_attempted": True,
            "rebuild_committed": bool(step.accepted),
            "rebuild_removed_entities": measured.get("removed_entities", 0.0),
            "rebuild_plate_rate_per_s": measured.get("plate_rate_per_s", 0.0),
            "rebuild_rate_retention": measured.get("rate_retention", 0.0),
            "rebuild_gain_threshold": gain_threshold,
        }
    )

    if step.accepted:
        journal.complete_stage(
            15,
            (
                "Dominated belt branch demolished and compact iron production "
                "retained within the configured survival threshold."
            ),
        )
        journal.event(
            "rebuild",
            "Transactional demolition committed after throughput-retention validation.",
            measurements=measured,
        )
        return True

    journal.complete_stage(
        15,
        "Demolition challenger rejected; checkpoint rollback preserved the original factory.",
    )
    journal.event(
        "rebuild",
        "Transactional demolition rolled back because the compact survivor did not dominate.",
        measurements=measured,
    )
    return True


LAB_PREUNLOCKED_PLANNING_GOALS = frozenset({
    "electronics_trigger",
    "lab_bootstrap",
    "lab_automation",
})


def update_engineering_frontier(
    journal: ResearchJournal,
    *,
    achieved: set[str],
    stalled_attempts: dict[str, int] | None = None,
) -> dict[str, Any]:
    stalled = dict(stalled_attempts or {})
    planning_assumptions = (
        LAB_PREUNLOCKED_PLANNING_GOALS
        if journal.state.get("arena", {}).get("technology") == "pre_unlocked"
        else frozenset()
    )
    planning_achieved = set(achieved) | set(planning_assumptions)
    state = EngineeringState(
        achieved=frozenset(planning_achieved),
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
    progression["validated_achieved"] = sorted(achieved)
    progression["planning_assumptions"] = sorted(planning_assumptions)
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
    safety_stock: int,
    producer_refuel: int,
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
    region = patch_bounds(patch)
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

    # One external coal lasts roughly one burner-drill fuel cycle. We wait
    # beyond that cycle before transferring mined coal back into the drill, so
    # the second production window is causally powered by endogenous fuel.
    bootstrap_seed = 1
    seed_seconds = max(30, settle_seconds)

    # ``patch_center`` is deterministic on a fixed map, so an heir aims its
    # drill at the tiles the ancestor's own coal cell stands on: the promoted
    # checkpoint holds a drill at (27, 9) with its chest at (27.5, 10.5),
    # which is exactly where this anchor snaps. Nothing is adopted -- the
    # claim of this stage is that the cell it built survived on coal that
    # cell mined, and an inherited drill is no evidence of that.
    survey = survey_world(env)
    drill_plan = plan_mining_cell(
        survey,
        center,
        adopt_names=TRIAL_ADOPTS_NOTHING,
        reach=PLACEMENT_SCAN_REACH,
        region=region,
    )
    journal.state["metrics"]["coal_placement"] = drill_plan.to_dict()
    if drill_plan.position is None:
        journal.fail_stage(
            6,
            f"Coal self-sufficiency found no free tiles on the patch: {drill_plan.reason}.",
        )
        journal.event(
            "refusal",
            "Coal self-sufficiency refused: the standing factory occupies the patch.",
            placement=drill_plan.to_dict(),
        )
        return False, center
    target = drill_plan.position

    # The cell is assembled out of the standing world. An heir carries the
    # kit its ancestor happened to hold, which for the promoted checkpoint is
    # no chest and no coal at all, and the seed this stage burns first has to
    # come from somewhere the world was measured to have it.
    supply = survey_stage_supply(
        env,
        anchor=(target[0], target[1] - SUPPLY_FURNACE_CLEARANCE),
        fuel_needed=bootstrap_seed,
        container_needed=True,
    )
    journal.state["metrics"]["coal_supply"] = supply_report(supply)
    journal.state["metrics"]["coal_bootstrap_seed"] = float(bootstrap_seed)
    blocked = blocking_refusals(supply)
    if blocked:
        journal.fail_stage(
            6,
            "Coal self-sufficiency refused: the standing world holds no kit for "
            f"its cell ({', '.join(blocked)}).",
        )
        journal.event(
            "refusal",
            "Coal self-sufficiency refused: no fuel or container in the world.",
            supply=journal.state["metrics"]["coal_supply"],
        )
        return False, center

    vault = quarantine_container(
        survey,
        anchor=target,
        exclude=(
            ()
            if supply is None or supply.salvage is None
            else (supply.salvage.position,)
        ),
    )
    journal.state["metrics"]["coal_quarantine_target"] = (
        None if vault is None else vault.to_dict()
    )
    # Everything the step still spends stays in the inventory: the seed, and
    # the charge the container smelt burns when the world had no spare chest.
    quarantine_keep = (
        bootstrap_seed
        if supply is None
        else max(bootstrap_seed, int(supply.fuel_needed))
    )
    journal.state["metrics"]["coal_quarantine_keeps"] = float(quarantine_keep)
    surplus = None if supply is None else max(0, supply.fuel_carried - quarantine_keep)
    if vault is None and surplus:
        journal.fail_stage(
            6,
            f"Coal self-sufficiency refused: {surplus} bootstrap coal and "
            f"{NO_QUARANTINE_IN_WORLD}.",
        )
        journal.event(
            "refusal",
            "Coal self-sufficiency refused: nowhere to quarantine bootstrap coal.",
            bootstrap_carried=surplus,
            reason=NO_QUARANTINE_IN_WORLD,
        )
        return False, center

    quarantine_prelude = coal_quarantine_script(vault, keep=quarantine_keep)
    supply_prelude = mining_cell_supply_script(supply, fuel_needed=bootstrap_seed)
    coal_container = cell_container(supply)
    container_binding = (
        f"coal_container_type = Prototype.{container_prototype(coal_container)}"
    )
    journal.state["metrics"]["coal_container"] = coal_container

    fast_reposition(env, x=target[0], y=target[1])
    output_before = production_output(namespace, "coal")
    clock = _StageClock(env)
    measured: dict[str, Any] = {}

    def validate_coal(result: Any) -> bool:
        clock.stop()
        measured["coal_world_before"] = output_before
        measured["coal_world_after"] = production_output(namespace, "coal")
        # An attribute the script never assigned is an abort, not a zero:
        # keep it unmeasured so the gate below refuses it instead of reading
        # it as a measured failure.
        for key in (
            "bootstrap_total",
            "bootstrap_quarantine",
            "coal_seed",
            "seed_phase_count",
            "transfer_1",
            "internal_stock_before",
            "internal_stock_after",
            "endogenous_growth",
            "endogenous_stockpile",
            "operational_refuel",
            "supply_fuel_drawn",
        ):
            measured[key] = _namespace_measure(namespace, key)
        measured["supply_draws"] = _supply_log_rows(
            getattr(namespace, "supply_fuel_log", None)
        )
        # Read off the chest this cell drops into. The world coal counter
        # over the same window also carries the inherited coal drill, which
        # keeps mining whatever this stage does, so a stage that took its
        # number from there would report the ancestor's flow as its own.
        measured["coal_output"] = _cell_total(
            measured,
            "seed_phase_count",
            "endogenous_growth",
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and _measured_above(measured, "coal_output")
            and _measured_at_least(measured, "transfer_1", 1)
            and _measured_above(measured, "endogenous_growth")
            and _measured_above(measured, "endogenous_stockpile")
        )

    code = f"""
{container_binding}
coal_drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target[0]},y={target[1]}),
    direction=Direction.DOWN,
)
{quarantine_prelude}
{supply_prelude}
coal_seed=min({bootstrap_seed},inspect_inventory()[Prototype.Coal])
if coal_seed>0:
    coal_drill=insert_item(
        Prototype.Coal,
        coal_drill,
        quantity=coal_seed,
    )
coal_chest=place_entity_next_to(
    coal_container_type,
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
    internal_stock_after-{safety_stock},
)
if available_for_refuel>0:
    operational_refuel=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min({producer_refuel},available_for_refuel),
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
    'coal_seed':coal_seed,
    'seed_phase_count':seed_phase_count,
    'transfer_1':transfer_1,
    'internal_stock_before':internal_stock_before,
    'internal_stock_after':internal_stock_after,
    'endogenous_growth':endogenous_growth,
    'endogenous_stockpile':endogenous_stockpile,
    'operational_refuel':operational_refuel,
    'supply_fuel_drawn':supply_fuel_drawn,
    'player_coal_after':inspect_inventory()[Prototype.Coal],
}})
"""
    step = executor.execute(
        code,
        accept=validate_coal,
        use_checkpoint_for_action=False,
    )
    drawn = {
        "coal_seed": measured.get("coal_seed"),
        "bootstrap_quarantined": measured.get("bootstrap_quarantine"),
        "supply_fuel_drawn": measured.get("supply_fuel_drawn"),
        "supply_draws": measured.get("supply_draws"),
    }
    journal.state["metrics"]["coal_supply_drawn"] = drawn

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
            placement=drill_plan.to_dict(),
        )
        return False, center

    output = float(measured["coal_output"] or 0.0)
    coal_chest = namespace.coal_chest
    duration = _record_observed_window(
        journal,
        clock,
        metric_prefix="coal_mining",
        fallback_seconds=float(seed_seconds + settle_seconds),
    )
    world_flow = max(
        0.0,
        float(measured["coal_world_after"]) - float(measured["coal_world_before"]),
    )
    journal.state["metrics"].update(
        {
            "coal_output": output,
            "coal_output_basis": COAL_OUTPUT_BASIS,
            # The whole surface over the same window, the ancestor's coal
            # drill included. Recorded beside the attributed reading so the
            # two are never confused for one another.
            "coal_world_flow": world_flow,
            "coal_rate_per_s": rate_per_second(output, duration),
            "coal_bootstrap_seed": measured["coal_seed"],
            "coal_bootstrap_quarantined": measured["bootstrap_quarantine"],
            "coal_endogenous_transfer": measured["transfer_1"],
            "coal_endogenous_growth": measured["endogenous_growth"],
            "coal_endogenous_stockpile": measured["endogenous_stockpile"],
            "coal_operational_refuel": measured["operational_refuel"],
            "coal_safety_stock_target": float(safety_stock),
            "coal_mining_reward": step.reward,
        }
    )
    coal_world = journal.state.setdefault("world", {}).setdefault("coal_patch", {})
    coal_world["output_chest"] = {
        "x": float(coal_chest.position.x),
        "y": float(coal_chest.position.y),
    }
    # The container this stage parked its bootstrap coal in was standing
    # before the stage ran; recording where it is keeps it apart from the
    # chest above, which this generation placed.
    coal_world["bootstrap_vault"] = (
        None
        if vault is None
        else {"x": vault.position[0], "y": vault.position[1], "origin": "adopted"}
    )

    accounting = journal.state["resource_accounting"]["exogenous_inputs"]["coal"]
    accounting.update(
        {
            "status": "self_sufficient",
            "validated_internal_production": output,
            "bootstrap_seed_used": measured["coal_seed"],
            "bootstrap_quarantined": measured["bootstrap_quarantine"],
            "endogenous_transfer": measured["transfer_1"],
            "endogenous_growth": measured["endogenous_growth"],
            "endogenous_stockpile": measured["endogenous_stockpile"],
            "operational_refuel": measured["operational_refuel"],
            "safety_stock_target": safety_stock,
        }
    )

    journal.complete_stage(
        6,
        (
            f"Coal survived endogenous refueling: {output:.0f} produced by this "
            f"cell, {_measured_text(measured, 'endogenous_stockpile')} buffered "
            f"internally, with {_measured_text(measured, 'bootstrap_quarantine')} "
            "bootstrap coal quarantined."
        ),
    )
    journal.event(
        "accept",
        "Coal extraction survived a second window using internally mined fuel.",
        coal_output=output,
        output_basis=COAL_OUTPUT_BASIS,
        bootstrap_seed=measured["coal_seed"],
        bootstrap_quarantined=measured["bootstrap_quarantine"],
        endogenous_transfer=measured["transfer_1"],
        endogenous_growth=measured["endogenous_growth"],
        placement=drill_plan.to_dict(),
        center={"x": center[0], "y": center[1]},
    )
    lesson = synthesize_lesson(
        stage="coal_self_sufficiency",
        facts={
            "accepted": True,
            "coal_output": output,
            "output_basis": COAL_OUTPUT_BASIS,
            "bootstrap_seed": measured["coal_seed"],
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
    safety_stock: int,
    fuel_budget: int,
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
    region = patch_bounds(patch)
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

    # The same collision the coal stage has: the promoted checkpoint holds a
    # copper drill at (-58, 83) with its chest at (-57.5, 84.5), and the exact
    # centre of this patch snaps onto it. Nothing is adopted -- the ore in the
    # ancestor's chest is not this generation's expansion.
    survey = survey_world(env)
    drill_plan = plan_mining_cell(
        survey,
        center,
        adopt_names=TRIAL_ADOPTS_NOTHING,
        reach=PLACEMENT_SCAN_REACH,
        region=region,
    )
    journal.state["metrics"]["copper_placement"] = drill_plan.to_dict()
    if drill_plan.position is None:
        journal.fail_stage(
            7,
            f"Copper expansion found no free tiles on the patch: {drill_plan.reason}.",
        )
        journal.event(
            "refusal",
            "Copper expansion refused: the standing factory occupies the patch.",
            placement=drill_plan.to_dict(),
        )
        return False, center
    target = drill_plan.position

    # The drill is fuelled once and never refuelled, so a charge that burns
    # out mid-window turns this measurement into a fuel measurement: output
    # was exactly floor(6.667 * coal), and the retention gate on copper was
    # arithmetically the test `coal_budget >= 2`. Size the charge from the
    # window instead, keeping the genome's budget as a floor so a challenger
    # can still choose to carry more.
    window_estimate = float(settle_seconds) + STAGE_OVERHEAD_SECONDS
    required_fuel = BURNER_MINING_DRILL.coal_for_seconds(window_estimate)
    fuel_budget = max(int(fuel_budget), required_fuel)

    # The cell's container comes from the world, like every other cell this
    # curriculum builds inside an inherited factory. The fuel does not: this
    # stage burns coal the previous one mined, which is the whole claim it
    # makes, so nothing here is drawn from the world for the drill.
    supply = survey_stage_supply(
        env,
        anchor=(target[0], target[1] - SUPPLY_FURNACE_CLEARANCE),
        fuel_needed=0,
        container_needed=True,
    )
    journal.state["metrics"]["copper_supply"] = supply_report(supply)
    blocked = blocking_refusals(supply)
    if blocked:
        journal.fail_stage(
            7,
            "Copper expansion refused: the standing world holds no container for "
            f"its cell ({', '.join(blocked)}).",
        )
        journal.event(
            "refusal",
            "Copper expansion refused: no container in the standing world.",
            supply=journal.state["metrics"]["copper_supply"],
        )
        return False, center
    supply_prelude = mining_cell_supply_script(supply, fuel_needed=0)
    copper_container = cell_container(supply)
    container_binding = (
        f"copper_container_type = Prototype.{container_prototype(copper_container)}"
    )
    journal.state["metrics"]["copper_container"] = copper_container

    fast_reposition(env, x=target[0], y=target[1])
    output_before = production_output(namespace, "copper-ore")
    clock = _StageClock(env)
    measured: dict[str, Any] = {}

    def validate_copper(result: Any) -> bool:
        clock.stop()
        measured["copper_world_before"] = output_before
        measured["copper_world_after"] = production_output(namespace, "copper-ore")
        # An attribute the script never assigned is an abort, not a zero.
        for key in (
            "copper_mining_fuel",
            "copper_drill_fuel",
            "copper_cell_output",
            "supply_fuel_drawn",
        ):
            measured[key] = _namespace_measure(namespace, key)
        measured["supply_draws"] = _supply_log_rows(
            getattr(namespace, "supply_fuel_log", None)
        )
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and _measured_above(measured, "copper_mining_fuel")
            and _measured_above(measured, "copper_drill_fuel")
            and _measured_above(measured, "copper_cell_output")
        )

    code = f"""
{container_binding}
move_to(coal_chest.position)
copper_mining_fuel=0
for _ in range(6):
    available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
    spendable=max(0,available_internal-{safety_stock})
    if spendable>0:
        break
    sleep(6)
available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
spendable=max(0,available_internal-{safety_stock})
if spendable>0:
    copper_mining_fuel=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min({fuel_budget},spendable),
    )

move_to(Position(x={target[0]},y={target[1]}))
copper_drill=place_entity(
    Prototype.BurnerMiningDrill,
    position=Position(x={target[0]},y={target[1]}),
    direction=Direction.DOWN,
)
{supply_prelude}
copper_drill_fuel=min(copper_mining_fuel,inspect_inventory()[Prototype.Coal])
if copper_drill_fuel>0:
    copper_drill=insert_item(
        Prototype.Coal,
        copper_drill,
        quantity=copper_drill_fuel,
    )
copper_chest=place_entity_next_to(
    copper_container_type,
    copper_drill.position,
    direction=Direction.DOWN,
)
sleep({settle_seconds})
copper_cell_output=inspect_inventory(copper_chest)[Prototype.CopperOre]
print({{
    'copper_cell_output':copper_cell_output,
    'copper_mining_fuel':copper_mining_fuel,
    'copper_drill_fuel':copper_drill_fuel,
    'supply_fuel_drawn':supply_fuel_drawn,
}})
"""
    step = executor.execute(
        code,
        accept=validate_copper,
        use_checkpoint_for_action=False,
    )
    journal.state["metrics"]["copper_supply_drawn"] = {
        "drill_coal_inserted": measured.get("copper_drill_fuel"),
        "endogenous_coal_extracted": measured.get("copper_mining_fuel"),
        "supply_fuel_drawn": measured.get("supply_fuel_drawn"),
        "supply_draws": measured.get("supply_draws"),
    }
    if not step.accepted:
        journal.fail_stage(
            7,
            "Copper mining produced no validated output; transaction rolled back.",
        )
        journal.event(
            "reject",
            "Copper expansion rejected and rolled back.",
            measurements=measured,
            placement=drill_plan.to_dict(),
        )
        return False, center

    # Read off the chest this cell drops into, never off the world counter:
    # the inherited copper drill keeps mining through the window, and its ore
    # would otherwise be counted as this generation's expansion.
    output = float(measured["copper_cell_output"] or 0.0)
    # Divide by the window that actually elapsed. Dividing by the sleep
    # literal inflated every copper rate roughly five-fold, because the game
    # ran through the whole step and not only through the sleep.
    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="copper_mining",
        fallback_seconds=float(settle_seconds),
    )
    journal.state["metrics"]["copper_ore_output"] = output
    journal.state["metrics"]["copper_ore_output_basis"] = COPPER_ORE_OUTPUT_BASIS
    journal.state["metrics"]["copper_ore_world_flow"] = max(
        0.0,
        float(measured["copper_world_after"]) - float(measured["copper_world_before"]),
    )
    journal.state["metrics"]["copper_mining_fuel_required"] = float(required_fuel)
    journal.state["metrics"]["copper_ore_rate_per_s"] = rate_per_second(
        output,
        window,
    )
    journal.state["metrics"]["copper_mining_reward"] = step.reward
    journal.state["metrics"]["copper_mining_internal_coal"] = measured[
        "copper_mining_fuel"
    ]
    journal.complete_stage(
        7,
        f"Copper mining accepted with {output:.0f} copper ore in its own chest.",
    )
    journal.event(
        "accept",
        "First persistent copper mining cell accepted.",
        copper_ore_output=output,
        output_basis=COPPER_ORE_OUTPUT_BASIS,
        placement=drill_plan.to_dict(),
        center={"x": center[0], "y": center[1]},
    )
    lesson = synthesize_lesson(
        stage="copper_mining",
        facts={
            "accepted": True,
            "copper_ore_output": output,
            "output_basis": COPPER_ORE_OUTPUT_BASIS,
            "endogenous_coal": measured["copper_mining_fuel"],
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
    safety_stock: int,
    fuel_budget: int,
    buffer_target: int,
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
    clock = _StageClock(env)
    measured: dict[str, Any] = {}

    # Same defect as the drill: a furnace charged once burns out partway and
    # the stage then reports fuel dose instead of smelting throughput. A
    # stone furnace draws 90 kW, so one coal lasts ~44 s of game time.
    window_estimate = float(settle_seconds) + STAGE_OVERHEAD_SECONDS
    required_fuel = STONE_FURNACE.coal_for_seconds(window_estimate)
    fuel_budget = max(int(fuel_budget), required_fuel)

    def validate_smelting(result: Any) -> bool:
        clock.stop()
        measured["copper_plate_world_before"] = plate_before
        measured["copper_plate_world_after"] = production_output(
            namespace,
            "copper-plate",
        )
        # An attribute the script never assigned is an abort, not a zero.
        for key in (
            "copper_ore_transfer",
            "copper_smelting_fuel",
            "copper_furnace_inventory",
        ):
            measured[key] = _namespace_measure(namespace, key)
        # Read off the furnace this stage placed. The inherited factory
        # smelts copper of its own -- the promoted checkpoint carries a
        # furnace at (-52, 89) -- and the world counter moves with it
        # whatever this stage does.
        measured["copper_plate_output"] = measured["copper_furnace_inventory"]
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and _measured_above(measured, "copper_ore_transfer")
            and _measured_above(measured, "copper_smelting_fuel")
            and _measured_above(measured, "copper_plate_output")
        )

    code = f"""
move_to(copper_chest.position)
copper_ore_available=inspect_inventory(copper_chest)[Prototype.CopperOre]
copper_ore_transfer=0
if copper_ore_available>0:
    copper_ore_transfer=extract_item(
        Prototype.CopperOre,
        copper_chest,
        quantity=min({buffer_target},copper_ore_available),
    )

move_to(coal_chest.position)
copper_smelting_fuel=0
for _ in range(6):
    available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
    spendable=max(0,available_internal-{safety_stock})
    if spendable>0:
        break
    sleep(6)
available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
spendable=max(0,available_internal-{safety_stock})
if spendable>0:
    copper_smelting_fuel=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min({fuel_budget},spendable),
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

    plates = float(measured["copper_plate_output"] or 0.0)
    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="copper_smelting",
        fallback_seconds=float(settle_seconds),
    )
    journal.state["metrics"]["copper_plate_output"] = plates
    journal.state["metrics"]["copper_plate_output_basis"] = COPPER_PLATE_OUTPUT_BASIS
    journal.state["metrics"]["copper_plate_world_flow"] = max(
        0.0,
        float(measured["copper_plate_world_after"])
        - float(measured["copper_plate_world_before"]),
    )
    journal.state["metrics"]["copper_smelting_fuel_required"] = float(required_fuel)
    journal.state["metrics"]["copper_plate_rate_per_s"] = rate_per_second(
        plates,
        window,
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
        output_basis=COPPER_PLATE_OUTPUT_BASIS,
        copper_ore_transfer=measured.get("copper_ore_transfer", 0.0),
        endogenous_coal=measured.get("copper_smelting_fuel", 0.0),
    )
    lesson = synthesize_lesson(
        stage="copper_smelting",
        facts={
            "accepted": True,
            "copper_plate_output": plates,
            "output_basis": COPPER_PLATE_OUTPUT_BASIS,
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
    fuel_budget: int,
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
    clock = _StageClock(env)
    measured: dict[str, Any] = {}

    code = f"""
move_to(coal_chest.position)
survival_transfer=0
survival_wait_seconds=0
for _ in range(8):
    available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
    if available_internal>={fuel_budget}:
        break
    sleep(6)
    survival_wait_seconds+=6
available_internal=inspect_inventory(coal_chest)[Prototype.Coal]
if available_internal>0:
    survival_transfer=extract_item(
        Prototype.Coal,
        coal_chest,
        quantity=min({fuel_budget},available_internal),
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
        clock.stop()
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
        # An attribute the script never assigned is an abort, not a zero.
        for key in (
            "refueled_count",
            "survival_transfer",
            "survival_wait_seconds",
            "survival_copper_feed",
            "survival_coal_reserve",
        ):
            measured[key] = _namespace_measure(namespace, key)
        return (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
            and _measured_at_least(measured, "survival_transfer", 5)
            and _measured_at_least(measured, "refueled_count", 5)
            and _measured_above(measured, "survival_copper_feed")
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
    # The fallback covers the sleep literal plus the fixed 8 s settle inside
    # the script; the wait loop ahead of them is exactly why the observed
    # window is the honest denominator here.
    total_window = _record_observed_window(
        journal,
        clock,
        metric_prefix="survival",
        fallback_seconds=float(settle_seconds + 8),
    )
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


def _bootstrap_vault_release_script() -> str:
    """FLE script that reopens the quarantined bootstrap coal.

    stage_coal_mining locks every bootstrap coal but one into a wooden chest so
    that the coal capability has to prove itself on coal it mined. Nothing ever
    reopened that chest: 359 coal were still sitting in it when generation 27
    stalled with every burner machine at coal=0. The stock is released only
    after the coal and survival gates have already been decided, so the
    endogenous-fuel evidence those stages produced stays intact.

    What comes back out is what this generation put in, never the stock the
    container held. In a world that already has a factory the quarantine is a
    container the world was already using -- ``quarantine_container`` picks
    one rather than placing one, because an heir carries no chest -- and the
    coal beside the bootstrap coal in it is the ancestor's, feeding a chain
    this stage may not empty. ``bootstrap_quarantine`` is the amount that went
    in, bound by the same script that put it there, and it is the ceiling
    here.
    """
    return """
fuel_vault_stock=0
fuel_vault_claim=0
fuel_vault_released=0
fuel_vault_note=''
try:
    move_to(bootstrap_vault.position)
    fuel_vault_stock=inspect_inventory(bootstrap_vault)[Prototype.Coal]
    fuel_vault_claim=min(fuel_vault_stock,bootstrap_quarantine)
    if fuel_vault_claim>0:
        fuel_vault_released=extract_item(
            Prototype.Coal,
            bootstrap_vault,
            quantity=fuel_vault_claim,
        )
except Exception as fuel_vault_exc:
    fuel_vault_note=str(fuel_vault_exc)[:120].replace('rror','rr0r').replace('xception','xcepti0n')
"""


def _fuel_feed_script(
    *,
    machines: tuple[tuple[str, str], ...],
    coal_per_machine: int,
) -> str:
    """FLE script giving each listed burner machine a chest and an inserter.

    A burner machine runs only while something keeps putting coal into it. This
    arena had no such thing: every unit of fuel arrived as a discrete
    insert_item dose, and the two burner inserters that existed moved ore. The
    feed built here is the standing version of that dose -- a wooden chest on
    the tile behind a burner inserter that drops into the machine. A burner
    inserter handling coal refuels itself from what it carries, so one primer
    unit is enough to start it.

    Four placement sides are tried per machine because pipes, belts and fluid
    connections make any fixed side unbuildable somewhere in this arena; a side
    that fails leaves nothing behind before the next one is tried. A machine
    that cannot be fed is recorded and skipped, never raised, so one crowded
    machine cannot roll back the feeds that did get built. Each machine is also
    resolved on its own, because naming them all in one tuple would let a
    single variable that an earlier rollback left undefined take every feed
    down with it.
    """
    resolution = "".join(
        f"""
try:
    fuel_machines.append(({variable},'{label}'))
except Exception:
    fuel_feed_log.append(('{label}',None,None,'machine variable is undefined'))
"""
        for variable, label in machines
    )
    machine_count = max(1, len(machines))
    return f"""
fuel_feed_log=[]
fuel_machines=[]
fuel_fed_count=0
fuel_coal_loaded_total=0
fuel_stock=inspect_inventory()[Prototype.Coal]
# Split what is carried evenly and hold one unit per machine back to prime its
# inserter. The per-machine target is the horizon charge; the split is what
# the released stock can actually cover.
fuel_share=max(0,(fuel_stock//{machine_count})-1)
fuel_dose=min({int(coal_per_machine)},fuel_share)
{resolution}
for fuel_machine,fuel_label in fuel_machines:
    fuel_inserter=None
    fuel_chest=None
    fuel_note=''
    for fuel_side,fuel_back in (
        (Direction.UP,Direction.DOWN),
        (Direction.DOWN,Direction.UP),
        (Direction.LEFT,Direction.RIGHT),
        (Direction.RIGHT,Direction.LEFT),
    ):
        if fuel_chest is None:
            try:
                move_to(fuel_machine.position)
                fuel_inserter=place_entity_next_to(
                    Prototype.BurnerInserter,
                    fuel_machine.position,
                    direction=fuel_side,
                    spacing=0,
                )
                fuel_inserter=rotate_entity(fuel_inserter,fuel_back)
                fuel_chest=place_entity_next_to(
                    Prototype.WoodenChest,
                    fuel_inserter.position,
                    direction=fuel_side,
                    spacing=0,
                )
            except Exception as fuel_exc:
                fuel_note=str(fuel_exc)[:120].replace('rror','rr0r').replace('xception','xcepti0n')
                if fuel_inserter is not None:
                    try:
                        pickup_entity(fuel_inserter)
                    except Exception:
                        fuel_note=fuel_note+' | pickup refused'
                fuel_inserter=None
                fuel_chest=None
    fuel_loaded=0
    fuel_primer=0
    if fuel_chest is not None:
        fuel_loaded=min(fuel_dose,inspect_inventory()[Prototype.Coal])
        if fuel_loaded>0:
            fuel_chest=insert_item(
                Prototype.Coal,
                fuel_chest,
                quantity=fuel_loaded,
            )
        if inspect_inventory()[Prototype.Coal]>0:
            fuel_inserter=insert_item(
                Prototype.Coal,
                fuel_inserter,
                quantity=1,
            )
            fuel_primer=1
        fuel_fed_count+=1
        fuel_coal_loaded_total+=fuel_loaded
    fuel_feed_log.append((fuel_label,fuel_loaded,fuel_primer,fuel_note))
"""


def _fuel_feed_rows(raw: Any) -> list[dict[str, Any]] | None:
    """Per-machine feed rows for the journal, or None when nothing was measured.

    The remote script appends one tuple per machine. A missing attribute means
    the script never reached the feed, and that is reported as None: defaulting
    it to 0.0 makes an aborted stage indistinguishable from a feed that loaded
    nothing, which is the substitution that cost eleven generations of
    misdirected diagnosis. A log whose shape does not match is discarded whole
    for the same reason -- a partially parsed list reads as a complete census
    of the feeds that were built.
    """
    if not isinstance(raw, (list, tuple)):
        return None
    rows: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            return None
        label, loaded, primer, note = entry
        text = str(note).strip() if note is not None else ""
        rows.append(
            {
                "machine": str(label),
                "coal_loaded": None if loaded is None else float(loaded),
                "inserter_primer_coal": None if primer is None else float(primer),
                "placement_note": text[:200] if text else None,
            }
        )
    return rows


def _fuel_feed_anchor(
    namespace: Any,
    machines: tuple[tuple[str, str], ...],
) -> tuple[float, float] | None:
    """Where the fuel draw is planned from: the first machine that is there.

    The order of ``machines`` is the fuel priority, so the anchor is the
    machine whose outage costs the most, and the containers nearest it are the
    ones the draw spends first. A machine variable no earlier stage bound is
    skipped rather than defaulted to the origin: a draw planned around tiles
    nobody read is the substitution this lab keeps paying for. When no machine
    answers at all the anchor is None, and the caller records an unsurveyed
    supply instead of inventing one.
    """
    for variable, _ in machines:
        position = getattr(getattr(namespace, variable, None), "position", None)
        if position is None:
            continue
        x = getattr(position, "x", None)
        y = getattr(position, "y", None)
        if x is None or y is None:
            try:
                x, y = position[0], position[1]
            except (IndexError, KeyError, TypeError):
                continue
        try:
            return (float(x), float(y))
        except (TypeError, ValueError):
            continue
    return None


def _fuel_feed_code(
    supply: SupplyPlan | None,
    *,
    machines: tuple[tuple[str, str], ...],
    coal_per_machine: int,
    fuel_needed: int,
) -> str:
    """The whole fuel-feed step: reopen, draw, build, report.

    The order is the contract. The vault gives back the coal this generation
    parked, the standing world covers the rest of the charge, and only then
    does the feed read the stock it divides across the machines. Generations
    44 and 46 ran the third part alone against the four to six coal an heir
    happened to inherit: ``max(0, 6 // 7 - 1)`` is zero, seven chests and
    seven burner inserters went up empty, ``coal_loaded_total`` was 0.0 and
    twelve entities were starving by the end of the generation. A draw
    spliced after the split would divide an inventory the coal has not
    reached yet, which reads the same in the journal and starves the same
    machines.
    """
    return (
        _bootstrap_vault_release_script()
        + mining_cell_supply_script(supply, fuel_needed=fuel_needed)
        + _fuel_feed_script(
            machines=machines,
            coal_per_machine=coal_per_machine,
        )
        + """
print({
    'fuel_vault_released':fuel_vault_released,
    'fuel_supply_drawn':supply_fuel_drawn,
    'fuel_fed_count':fuel_fed_count,
    'fuel_coal_loaded_total':fuel_coal_loaded_total,
    'fuel_dose':fuel_dose,
})
"""
    )


def _install_fuel_feeds(
    executor: TransactionalFLEExecutor,
    env: Any,
    namespace: Any,
    journal: ResearchJournal,
    *,
    machines: tuple[tuple[str, str], ...] = FUEL_FED_MACHINES,
) -> dict[str, Any]:
    """Convert the quarantined coal stock into standing fuel capacity.

    Runs as its own transaction after the stage that owns it has already been
    validated, so a feed that cannot be built rolls back only itself. The
    charge is sized from the generation horizon with the drill figures read
    from the runtime; the boiler burns at whatever the electric network draws,
    a rate not measured here, so the time its charge covers is reported as
    unknown instead of being derived from a nominal number.

    The stock that charge is split from is the world's, not the inventory's.
    An heir arrives carrying whatever its ancestor happened to hold and
    quarantines nothing, so the vault release returns nothing and the split
    has nothing to divide. The draw that fills the chests is the one stages 1,
    2, 6 and 7 already make: the same plan, the same ordering, and a reserve
    left in every container that is feeding something.
    """
    coal_per_machine = BURNER_MINING_DRILL.coal_for_seconds(
        LAB_GENERATION_HORIZON_SECONDS
    )
    # Every machine's horizon charge plus the unit that starts its inserter.
    fuel_needed = len(machines) * (int(coal_per_machine) + FUEL_FEED_PRIMER_COAL)
    anchor = _fuel_feed_anchor(namespace, machines)
    supply = (
        None
        if anchor is None
        else survey_stage_supply(
            env,
            anchor=anchor,
            fuel_needed=fuel_needed,
            container_needed=False,
            chain_reserve=int(FUEL_CHAIN_RESERVE_COAL),
        )
    )
    fuel_code = _fuel_feed_code(
        supply,
        machines=machines,
        coal_per_machine=coal_per_machine,
        fuel_needed=fuel_needed,
    )
    step = executor.execute(
        fuel_code,
        accept=lambda result: (
            not bool(result.info.get("error_occurred"))
            and result.candidate_game_state is not None
        ),
        use_checkpoint_for_action=False,
        # Building the feed is an investment that removes future carrying, not
        # carrying. Counting it as manual logistics would score installing
        # automation as a regression.
        purpose="infrastructure",
    )

    measured: dict[str, float | None] = {}
    for key in (
        "fuel_stock",
        "fuel_dose",
        "fuel_fed_count",
        "fuel_coal_loaded_total",
        "fuel_vault_stock",
        "fuel_vault_claim",
        "fuel_vault_released",
        "supply_fuel_drawn",
    ):
        value = getattr(namespace, key, None)
        measured[key] = None if value is None else float(value)
    dose = measured["fuel_dose"]
    vault_note = getattr(namespace, "fuel_vault_note", None)
    payload: dict[str, Any] = {
        "committed": bool(step.accepted),
        "horizon_s": LAB_GENERATION_HORIZON_SECONDS,
        "coal_target_per_machine": float(coal_per_machine),
        "coal_per_machine_effective": dose,
        "drill_covered_seconds": (
            None
            if dose is None
            else dose * BURNER_MINING_DRILL.seconds_per_coal()
        ),
        "boiler_covered_seconds": None,
        "machines_planned": [label for _, label in machines],
        "machines_fed": measured["fuel_fed_count"],
        "coal_loaded_total": measured["fuel_coal_loaded_total"],
        "coal_carried_before": measured["fuel_stock"],
        "coal_needed_total": float(fuel_needed),
        # What the standing world contributed, kept apart from the vault: a
        # generation that drew its fuel from containers it found is not the
        # same reading as one that reopened its own quarantine.
        "coal_drawn_from_world": measured["supply_fuel_drawn"],
        "world_draws": _supply_log_rows(
            getattr(namespace, "supply_fuel_log", None)
        ),
        "supply": supply_report(supply),
        "supply_anchor": (
            None if anchor is None else {"x": anchor[0], "y": anchor[1]}
        ),
        "vault_stock": measured["fuel_vault_stock"],
        # What the release was allowed to take: the coal this generation
        # quarantined, never the whole stock of a container the world was
        # already using.
        "vault_claim": measured["fuel_vault_claim"],
        "vault_released": measured["fuel_vault_released"],
        "vault_note": (
            str(vault_note)[:200] or None if vault_note is not None else None
        ),
        "feeds": _fuel_feed_rows(getattr(namespace, "fuel_feed_log", None)),
        "step_result": _step_error_text(step.info),
    }
    # Keyed by stage so a second installation point cannot silently overwrite
    # the record of the first one.
    stage_key = str(journal.state.get("stage") or "unknown")
    journal.state["metrics"].setdefault("fuel_feeds", {})[stage_key] = payload
    journal.event(
        "fuel_feed",
        (
            "Coal chests and burner inserters installed on the arena's burner "
            "machines."
            if step.accepted
            else "Fuel-feed transaction rejected; dose-based fuelling remains."
        ),
        fuel_feeds=payload,
    )
    return payload


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

    # The boiler exists from here on, and both the coal gate and the survival
    # soak have already been decided, so this is the first point where the
    # quarantined bootstrap stock can be reopened without touching the
    # endogenous-fuel evidence those stages produced. Every burner machine in
    # the arena gets its standing feed in the same transaction.
    feeds = _install_fuel_feeds(executor, env, namespace, journal)

    journal.complete_stage(
        10,
        f"Steam power accepted with {measured['energy']:.0f} J stored energy.",
    )
    journal.event(
        "accept",
        "Offshore pump, boiler and steam engine formed a working power system.",
        measurements=measured,
        fuel_feeds=feeds,
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
    clock = _StageClock(env)
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
        clock.stop()
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
    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="iron_gear_wheel",
        fallback_seconds=float(settle_seconds),
    )
    journal.state["metrics"]["iron_gear_wheel_output"] = output
    journal.state["metrics"]["iron_gear_wheel_rate_per_s"] = rate_per_second(
        output,
        window,
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
    clock = _StageClock(env)
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
        clock.stop()
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
    window = _record_observed_window(
        journal,
        clock,
        metric_prefix="automation_science",
        fallback_seconds=float(settle_seconds),
    )
    journal.state["metrics"]["automation_science_output"] = output
    journal.state["metrics"]["automation_science_rate_per_s"] = rate_per_second(
        output,
        window,
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


def bottleneck_stage_name(previous_research: dict[str, Any]) -> str | None:
    """The stage the previous run got stuck on.

    The generation report already defines the bottleneck as the first failed
    stage, and this keeps that definition instead of inventing a second one.
    A run that failed nothing is bottlenecked on wherever it stopped.
    """
    curriculum = previous_research.get("curriculum")
    if isinstance(curriculum, list):
        for stage in curriculum:
            if isinstance(stage, dict) and stage.get("status") == "failed":
                name = stage.get("name")
                if isinstance(name, str) and name:
                    return name
    stage_name = previous_research.get("stage")
    if isinstance(stage_name, str) and stage_name:
        return stage_name
    return None


def knowledge_stage_for(curriculum_stage: str | None) -> str | None:
    """The knowledge-log slug a curriculum stage records its lessons under."""
    if curriculum_stage is None:
        return None
    return KNOWLEDGE_STAGE_BY_CURRICULUM_NAME.get(curriculum_stage)


def knowledge_recall_for_run(previous_research: dict[str, Any]) -> KnowledgeRecall:
    """The verified lessons this generation is entitled to decide with.

    Relevance is the stage of the previous run's bottleneck: lessons written
    under that stage are the ones about the obstacle still standing.
    """
    return recall_verified_lessons(
        KNOWLEDGE_LOG,
        stage=knowledge_stage_for(bottleneck_stage_name(previous_research)),
        limit=KNOWLEDGE_RECALL_LIMIT,
    )


def recalled_knowledge_report(evolution: dict[str, Any]) -> dict[str, Any]:
    """What the generation report says about the knowledge it decided with.

    A generation that never consulted the log says so, because a missing
    field would be read as a generation that consulted it and found nothing.
    """
    recorded = evolution.get("knowledge_recall")
    if isinstance(recorded, dict):
        return recorded
    return recall_not_consulted().to_dict()


def build_advisor_context(
    *,
    champion_configuration: dict[str, Any],
    previous_research: dict[str, Any],
    candidate: dict[str, Any],
    recall: KnowledgeRecall,
) -> dict[str, Any]:
    """The evidence handed to the advisor, recalled knowledge included.

    The counts of both populations travel with the lessons so the advice
    cannot be read as using knowledge without showing how much was discarded.
    """
    previous_evolution = previous_research.get("evolution")
    return {
        "champion_configuration": champion_configuration,
        "previous_run": {
            "run_id": previous_research.get("run_id"),
            "arena": previous_research.get("arena"),
            "status": previous_research.get("status"),
            "stage": previous_research.get("stage"),
            "detail": previous_research.get("detail"),
            "next_action": previous_research.get("next_action"),
            "promotion": previous_evolution.get("promotion")
            if isinstance(previous_evolution, dict)
            else None,
            "metrics": previous_research.get("metrics", {}),
        },
        "candidate_before_advice": candidate,
        **recall.to_advisor_context(),
    }


def finalize_evolution_selection(
    journal: ResearchJournal,
    *,
    achieved: set[str],
    physical_graph: dict[str, Any] | None = None,
    inherited_capabilities: InheritedCapabilities | None = None,
) -> dict[str, Any]:
    stages = [
        stage
        for stage in journal.state.get("curriculum", [])
        if isinstance(stage, dict)
    ]
    failed_stage_names = [
        str(stage.get("name"))
        for stage in stages
        if stage.get("status") == "failed"
    ]
    completed_stage_names = [
        str(stage.get("name"))
        for stage in stages
        if stage.get("status") == "completed"
    ]
    failed_stages = len(failed_stage_names)
    # The names travel with the fitness so the next comparison can tell a lost
    # capability from an unreached frontier. Passing only the count is what
    # rejected twelve generations for going further than the incumbent.
    # What the generation started holding travels with the fitness, so the
    # comparison can tell what this genome built from what an ancestor handed
    # it. None means no inheritance was in play; an inheritance that could not
    # be resolved is stated as such and withholds credit rather than granting
    # it silently.
    challenger = fitness_from_research(
        metrics=journal.state.get("metrics", {}),
        achieved=achieved,
        resource_accounting=journal.state.get("resource_accounting", {}),
        physical_graph=physical_graph,
        failed_stages=failed_stages,
        completed_stage_names=completed_stage_names,
        failed_stage_names=failed_stage_names,
        inherited_capabilities=inherited_capabilities,
    )

    incumbent = incumbent_champion()
    incumbent_fitness: FitnessVector | None = None
    if isinstance(incumbent.get("fitness"), dict):
        incumbent_fitness = FitnessVector.from_dict(incumbent["fitness"])

    evolution = journal.state.setdefault("evolution", {})
    evolution["inherited_capabilities"] = (
        {"in_play": False}
        if inherited_capabilities is None
        else {"in_play": True, **inherited_capabilities.to_dict()}
    )
    retention_ratio = float(evolution.get("retention_ratio", 0.80) or 0.80)
    decision = compare_challenger(
        incumbent_fitness,
        challenger,
        retention_ratio=retention_ratio,
    )

    metrics = journal.state.get("metrics", {})
    configuration = dict(
        evolution.get("challenger", {}).get("configuration", {})
    )
    configuration.update(
        {
            "placement_best_arm": metrics.get("placement_best_arm"),
            "placement_ucb_best_arm": metrics.get("placement_ucb_best_arm"),
        }
    )
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

    # The archive is offered the challenger only after the champion file is
    # written: the incumbent comparison above stays the reference, and this
    # adds the lineages that comparison discards without changing it. A
    # refusal is kept as a refusal, counted and reported, because a candidate
    # whose behaviour could not be computed has no niche to be filed under.
    archive, archive_status = load_niche_archive()
    archive_report: dict[str, Any] = dict(archive_status)
    if archive is not None:
        insertion = archive.insert(challenger, record=candidate_record)
        archive.save(NICHE_ARCHIVE)
        archive_report = {
            **archive_status,
            **insertion.to_dict(),
            "niche_count": len(archive),
            "refusal_count": len(archive.refusals()),
            "refusal_counts": archive.refusal_counts(),
        }
    evolution["archive"] = archive_report

    selected_at = str(candidate_record["selected_at"])
    started_at = journal.state.get("started_at")
    duration_s: float | None = None
    if isinstance(started_at, str):
        try:
            duration_s = max(
                0.0,
                (
                    datetime.fromisoformat(selected_at)
                    - datetime.fromisoformat(started_at)
                ).total_seconds(),
            )
        except ValueError:
            duration_s = None

    report = {
        "at": selected_at,
        "generation": generation,
        "code_revision": code_revision(),
        "run_id": journal.run_id,
        "challenger": candidate_record,
        "incumbent_run_id": incumbent.get("run_id") if incumbent else None,
        "incumbent_generation": incumbent.get("generation") if incumbent else None,
        "decision": decision.to_dict(),
        "archive": evolution.get("archive"),
        "parent": evolution.get("parent"),
        "inherited_capabilities": evolution.get("inherited_capabilities"),
        "knowledge_recall": recalled_knowledge_report(evolution),
        "bottleneck": failed_stage_names[0] if failed_stage_names else None,
        "failed_stages": failed_stage_names,
        "completed_stages": completed_stage_names,
        "completed_stage_count": len(completed_stage_names),
        "total_stage_count": len(journal.state.get("curriculum", [])),
        "duration_s": duration_s,
        "next_action": journal.state.get("next_action"),
        "engineering_progression": journal.state.get(
            "engineering_progression",
            {},
        ),
        "production_plans": journal.state.get("production_plans", {}),
        "metrics": journal.state.get("metrics", {}),
    }
    append_jsonl(EVOLUTION_HISTORY, report)
    atomic_json(
        GENERATION_REPORTS / f"generation-{generation:04d}-{journal.run_id}.json",
        report,
    )
    journal.state["generation_report"] = {
        "path": str(
            GENERATION_REPORTS
            / f"generation-{generation:04d}-{journal.run_id}.json"
        ),
        "bottleneck": report["bottleneck"],
        "completed_stage_count": report["completed_stage_count"],
        "total_stage_count": report["total_stage_count"],
    }
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
    inherited_capabilities: InheritedCapabilities | None = None,
) -> dict[str, Any]:
    """Run one lab generation.

    ``inherited_capabilities`` describes what the world already held when the
    generation started, for the caller that warm-started it from a promoted
    ancestor. None is the cold start: nothing was inherited, so every
    capability reached is this genome's.
    """
    import gym

    list_environments()
    env = gym.make("iron_ore_throughput", run_idx=0)
    run_id = datetime.now(UTC).strftime("curriculum-%Y%m%dT%H%M%SZ")
    previous_research = read_json_object(RESEARCH_STATE)
    journal = ResearchJournal(run_id)
    executor = TransactionalFLEExecutor(
        env,
        runtime_context=lambda: {
            "run_id": journal.run_id,
            "arena": "lab_play",
            "stage": journal.state.get("stage"),
            "run_status": journal.state.get("status"),
            "progress": journal.state.get("progress"),
        },
    )
    world_lease = FactorioWorldLease(
        run_id=run_id,
        arena="lab_play",
        owner="curriculum_runner",
    ).acquire()
    evolution = journal.state["evolution"]
    champion = evolution.get("champion") or {}
    champion_configuration = (
        champion.get("configuration", {})
        if isinstance(champion, dict)
        else {}
    )
    generation = int(evolution.get("generation", 1) or 1)
    # The mutation departs from an archived elite when there is one, and from
    # the global champion otherwise. The advisor below still reads the global
    # champion: it is the reference of the promotion decision, which this does
    # not change.
    archive, archive_status = load_niche_archive()
    parent_configuration, parent_provenance = select_mutation_parent(
        champion_configuration=champion_configuration,
        archive=archive,
        rng=parent_sampling_rng(generation=generation, seed=seed),
    )
    evolution["parent"] = {**archive_status, **parent_provenance}
    base_genome = challenger_genome(
        attempt=generation,
        champion_configuration=parent_configuration,
        default_exploration=exploration,
        seed=seed,
    )
    # Lessons that passed verification are recalled before the advice is
    # asked for, and the recall travels into the generation report so the
    # advice can later be attributed to the knowledge it was given.
    recall = knowledge_recall_for_run(previous_research)
    evolution["knowledge_recall"] = recall.to_dict()
    advice = propose_evolution_advice(
        build_advisor_context(
            champion_configuration=champion_configuration,
            previous_research=previous_research,
            candidate=base_genome.to_dict(),
            recall=recall,
        )
    )
    genome = apply_advice(base_genome, advice.adjustments)
    evolution["advisor"] = advice.to_dict()
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
        advisor=advice.to_dict(),
    )

    try:
        executor.reset(seed=seed)
        journal.event(
            "checkpoint",
            "Deterministic curriculum environment reset.",
            seed=seed,
        )

        patch, center = stage_baseline(
            executor,
            env,
            journal,
            settle_seconds=baseline_settle,
        )
        # The placement stages may move a blocked cell, and a cell moved off
        # the ore places and mines nothing, so the measured patch bounds how
        # far they may move it.
        placement_region = patch_bounds(patch)
        best_arm = stage_online_learning(
            executor,
            env,
            journal,
            center=center,
            episodes=placement_episodes,
            settle_seconds=trial_settle,
            exploration=effective_exploration,
            radius_scale=genome.placement_radius_scale,
            region=placement_region,
        )
        stage_scale_mining(
            executor,
            env,
            journal,
            center=center,
            best_arm=best_arm,
            settle_seconds=scale_settle,
            radius_scale=genome.placement_radius_scale,
            region=placement_region,
        )
        smelting_ok = stage_smelting_probe(
            executor,
            env,
            journal,
            center=center,
            settle_seconds=smelt_settle,
            region=placement_region,
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
                region=placement_region,
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
                safety_stock=genome.coal_safety_stock,
                producer_refuel=genome.coal_producer_refuel,
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
                safety_stock=genome.coal_safety_stock,
                fuel_budget=genome.coal_copper_mining_budget,
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
                    safety_stock=genome.coal_safety_stock,
                    fuel_budget=genome.coal_copper_smelting_budget,
                    buffer_target=genome.buffer_target,
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
                fuel_budget=genome.coal_survival_budget,
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
        circuits_ok = False
        logistic_science_ok = False
        optimization_ok = False
        if science_ok:
            achieved.add("automation_science")
            achieved.add("assembler_gears")
            circuits_ok = stage_electronic_circuits(
                executor,
                env,
                journal,
                settle_seconds=18,
            )
        if circuits_ok:
            achieved.add("electronic_circuits")
            logistic_science_ok = stage_logistic_science(
                executor,
                env,
                journal,
                settle_seconds=24,
            )
        if logistic_science_ok:
            achieved.add("logistic_science")
            optimization_ok = stage_transactional_rebuild(
                executor,
                env,
                journal,
                logistics=logistics,
                settle_seconds=18,
                gain_threshold=genome.rebuild_gain_threshold,
            )

        progression = update_engineering_frontier(
            journal,
            achieved=achieved,
        )

        intervention_snapshot = executor.intervention_snapshot()
        journal.state["metrics"]["interventions"] = {
            **intervention_snapshot,
            "post_bootstrap_committed": dict(
                intervention_snapshot.get("committed", {})
            ),
            "scope": "entire_lab_generation",
        }

        physical_graph: dict[str, Any] | None = None
        try:
            physical_entities = env.unwrapped.instance.namespace._save_entity_state(
                distance=500,
                player_entities=True,
                resource_entities=False,
                items_on_ground=False,
                encode=False,
                compress=False,
            )
            physical_graph = build_factory_graph(physical_entities)
            journal.state["metrics"]["physical_factory_graph"] = dict(
                physical_graph.get("metrics", {})
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            journal.state["metrics"]["physical_factory_graph_error"] = (
                f"{type(exc).__name__}: {exc}"
            )

        selection = finalize_evolution_selection(
            journal,
            achieved=achieved,
            physical_graph=physical_graph,
            inherited_capabilities=inherited_capabilities,
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
                and circuits_ok
                and logistic_science_ok
                and optimization_ok
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
        world_lease.release()


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
