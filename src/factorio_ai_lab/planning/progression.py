from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EngineeringGoal:
    goal_id: str
    label: str
    kind: str
    prerequisites: frozenset[str] = frozenset()
    unlock_value: float = 1.0
    estimated_cost: float = 1.0
    target_item_rates: Mapping[str, float] = field(default_factory=dict)
    target_entities: Mapping[str, int] = field(default_factory=dict)
    target_research: frozenset[str] = frozenset()
    introduces: frozenset[str] = frozenset()

    def is_satisfied(self, state: EngineeringState) -> bool:
        if self.goal_id in state.achieved:
            return True

        for item, minimum in self.target_item_rates.items():
            if float(state.item_rates.get(item, 0.0)) < float(minimum):
                return False

        for entity, minimum in self.target_entities.items():
            if int(state.entity_counts.get(entity, 0)) < int(minimum):
                return False

        if not self.target_research.issubset(state.researched):
            return False

        has_any_criterion = bool(
            self.target_item_rates
            or self.target_entities
            or self.target_research
        )
        return has_any_criterion


@dataclass(frozen=True)
class EngineeringState:
    achieved: frozenset[str] = frozenset()
    item_rates: Mapping[str, float] = field(default_factory=dict)
    entity_counts: Mapping[str, int] = field(default_factory=dict)
    researched: frozenset[str] = frozenset()
    stalled_attempts: Mapping[str, int] = field(default_factory=dict)

    @property
    def produced_items(self) -> frozenset[str]:
        return frozenset(
            item
            for item, rate in self.item_rates.items()
            if float(rate) > 0.0
        )


@dataclass(frozen=True)
class GoalCandidate:
    goal: EngineeringGoal
    score: float
    novelty: float
    retry_penalty: float


class ProductionEngineeringPlanner:
    """
    Capability-frontier planner for open-ended factory progression.

    The planner intentionally separates *what should be built next* from the
    executor that performs Factorio actions. Goals become eligible when their
    prerequisites are satisfied. Repeated failures penalize a goal rather than
    terminating the curriculum, so another feasible branch can be explored.
    """

    def __init__(self, goals: tuple[EngineeringGoal, ...]) -> None:
        if not goals:
            raise ValueError("at least one engineering goal is required")

        ids = [goal.goal_id for goal in goals]
        if len(ids) != len(set(ids)):
            raise ValueError("engineering goal ids must be unique")

        known = set(ids)
        for goal in goals:
            unknown = set(goal.prerequisites) - known
            if unknown:
                raise ValueError(
                    f"goal {goal.goal_id!r} has unknown prerequisites: "
                    f"{sorted(unknown)}"
                )

        self.goals = goals
        self._by_id = {goal.goal_id: goal for goal in goals}

    def inferred_achieved(self, state: EngineeringState) -> frozenset[str]:
        claimed = set(state.achieved)
        achieved: set[str] = set()
        changed = True
        while changed:
            changed = False
            for goal in self.goals:
                if goal.goal_id in achieved:
                    continue
                if not goal.prerequisites.issubset(achieved):
                    continue
                candidate_state = EngineeringState(
                    achieved=frozenset(achieved),
                    item_rates=state.item_rates,
                    entity_counts=state.entity_counts,
                    researched=state.researched,
                    stalled_attempts=state.stalled_attempts,
                )
                if (
                    goal.goal_id in claimed
                    or goal.is_satisfied(candidate_state)
                ):
                    achieved.add(goal.goal_id)
                    changed = True
        return frozenset(achieved)

    def dependency_debt(
        self,
        state: EngineeringState,
    ) -> tuple[dict[str, object], ...]:
        valid = self.inferred_achieved(state)
        debt: list[dict[str, object]] = []
        for goal_id in sorted(set(state.achieved) - set(valid)):
            goal = self._by_id.get(goal_id)
            if goal is None:
                continue
            missing = sorted(set(goal.prerequisites) - set(valid))
            debt.append(
                {
                    "goal_id": goal.goal_id,
                    "label": goal.label,
                    "missing_prerequisites": missing,
                }
            )
        return tuple(debt)

    def frontier(self, state: EngineeringState) -> tuple[EngineeringGoal, ...]:
        achieved = self.inferred_achieved(state)
        return tuple(
            goal
            for goal in self.goals
            if goal.goal_id not in achieved
            and goal.prerequisites.issubset(achieved)
        )

    @staticmethod
    def _novelty(goal: EngineeringGoal, state: EngineeringState) -> float:
        if not goal.introduces:
            return 0.0
        unseen = goal.introduces - state.produced_items
        return len(unseen) / len(goal.introduces)

    def score(
        self,
        goal: EngineeringGoal,
        state: EngineeringState,
    ) -> GoalCandidate:
        novelty = self._novelty(goal, state)
        attempts = int(state.stalled_attempts.get(goal.goal_id, 0))
        retry_penalty = 1.75 * attempts

        # High unlock value and diversification are rewarded. Cost and repeated
        # failed attempts are explicit penalties, so the agent does not become
        # trapped optimizing one mature production line forever.
        score = (
            4.0 * float(goal.unlock_value)
            + 3.0 * novelty
            - 0.35 * float(goal.estimated_cost)
            - retry_penalty
        )
        return GoalCandidate(
            goal=goal,
            score=score,
            novelty=novelty,
            retry_penalty=retry_penalty,
        )

    def ranked_frontier(
        self,
        state: EngineeringState,
    ) -> tuple[GoalCandidate, ...]:
        candidates = [self.score(goal, state) for goal in self.frontier(state)]
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    -candidate.score,
                    candidate.goal.estimated_cost,
                    candidate.goal.goal_id,
                ),
            )
        )

    def next_goal(self, state: EngineeringState) -> EngineeringGoal | None:
        ranked = self.ranked_frontier(state)
        return ranked[0].goal if ranked else None


EARLY_GAME_ENGINEERING_GOALS: tuple[EngineeringGoal, ...] = (
    EngineeringGoal(
        goal_id="iron_backbone",
        label="Stable iron mining and smelting backbone",
        kind="throughput",
        unlock_value=5.0,
        estimated_cost=2.0,
        target_item_rates={"iron-plate": 1.0},
        introduces=frozenset({"iron-ore", "iron-plate"}),
    ),
    EngineeringGoal(
        goal_id="coal_mining",
        label="Establish self-sufficient coal mining",
        kind="resource_expansion",
        prerequisites=frozenset({"iron_backbone"}),
        unlock_value=8.0,
        estimated_cost=2.0,
        target_item_rates={"coal": 0.1},
        introduces=frozenset({"coal"}),
    ),
    EngineeringGoal(
        goal_id="copper_mining",
        label="Discover and mine copper",
        kind="resource_expansion",
        prerequisites=frozenset({"iron_backbone"}),
        unlock_value=7.0,
        estimated_cost=2.0,
        target_item_rates={"copper-ore": 1.0},
        introduces=frozenset({"copper-ore"}),
    ),
    EngineeringGoal(
        goal_id="steam_power",
        label="Establish a powered factory bus",
        kind="infrastructure",
        prerequisites=frozenset({"iron_backbone", "coal_mining"}),
        unlock_value=5.5,
        estimated_cost=4.0,
        target_entities={
            "boiler": 1,
            "steam-engine": 1,
            "medium-electric-pole": 1,
        },
        introduces=frozenset({"electric-power"}),
    ),
    EngineeringGoal(
        goal_id="copper_smelting",
        label="Automate copper plate production",
        kind="throughput",
        prerequisites=frozenset({"copper_mining", "coal_mining"}),
        unlock_value=7.5,
        estimated_cost=3.0,
        target_item_rates={"copper-plate": 1.0},
        introduces=frozenset({"copper-plate"}),
    ),
    EngineeringGoal(
        goal_id="electronics_trigger",
        label="Unlock electronics from sustained copper-plate production",
        kind="technology_trigger",
        prerequisites=frozenset({"copper_smelting"}),
        unlock_value=8.0,
        estimated_cost=2.0,
        target_research=frozenset({"electronics"}),
        introduces=frozenset(
            {"copper-cable", "electronic-circuit", "small-electric-pole"}
        ),
    ),
    EngineeringGoal(
        goal_id="lab_bootstrap",
        label="Craft a lab and unlock automation science",
        kind="technology_trigger",
        prerequisites=frozenset({"steam_power", "electronics_trigger"}),
        unlock_value=9.0,
        estimated_cost=3.0,
        target_entities={"lab": 1},
        target_research=frozenset({"automation-science-pack"}),
        introduces=frozenset({"lab", "automation-science-pack"}),
    ),
    EngineeringGoal(
        goal_id="automation_science",
        label="Produce automation science packs",
        kind="science",
        prerequisites=frozenset({"lab_bootstrap"}),
        unlock_value=10.0,
        estimated_cost=4.0,
        target_item_rates={"automation-science-pack": 0.1},
        introduces=frozenset(
            {"iron-gear-wheel", "copper-plate", "automation-science-pack"}
        ),
    ),
    EngineeringGoal(
        goal_id="lab_automation",
        label="Research Automation in a working lab",
        kind="technology",
        prerequisites=frozenset({"automation_science"}),
        unlock_value=10.5,
        estimated_cost=3.0,
        target_entities={"lab": 1},
        target_research=frozenset({"automation"}),
        introduces=frozenset({"automation"}),
    ),
    EngineeringGoal(
        goal_id="assembler_gears",
        label="Move iron gear production into an assembling machine",
        kind="automation",
        prerequisites=frozenset({"lab_automation", "steam_power"}),
        unlock_value=8.5,
        estimated_cost=4.0,
        target_entities={"assembling-machine-1": 1},
        target_item_rates={"iron-gear-wheel": 0.1},
        introduces=frozenset({"assembling-machine-1", "iron-gear-wheel"}),
    ),
    EngineeringGoal(
        goal_id="electronic_circuits",
        label="Automate copper cable and electronic circuits",
        kind="automation",
        prerequisites=frozenset({"assembler_gears", "copper_smelting"}),
        unlock_value=10.0,
        estimated_cost=5.0,
        target_item_rates={"electronic-circuit": 0.1},
        introduces=frozenset({"copper-cable", "electronic-circuit"}),
    ),
    EngineeringGoal(
        goal_id="logistic_science",
        label="Produce logistic science packs",
        kind="science",
        prerequisites=frozenset({"electronic_circuits", "automation_science"}),
        unlock_value=11.0,
        estimated_cost=6.0,
        target_item_rates={"logistic-science-pack": 0.1},
        introduces=frozenset({"logistic-science-pack"}),
    ),
    EngineeringGoal(
        goal_id="research_logistics",
        label="Research logistics technologies from red/green science",
        kind="technology",
        prerequisites=frozenset({"logistic_science"}),
        unlock_value=11.5,
        estimated_cost=4.0,
        target_research=frozenset({"logistics"}),
        introduces=frozenset({"logistics"}),
    ),
    EngineeringGoal(
        goal_id="electric_mining",
        label="Upgrade extraction to electric mining drills",
        kind="capacity_upgrade",
        prerequisites=frozenset({"steam_power", "electronic_circuits"}),
        unlock_value=9.5,
        estimated_cost=5.0,
        target_entities={"electric-mining-drill": 1},
        introduces=frozenset({"electric-mining-drill"}),
    ),
)


DEFAULT_ENGINEERING_PLANNER = ProductionEngineeringPlanner(
    EARLY_GAME_ENGINEERING_GOALS
)
