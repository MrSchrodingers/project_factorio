"""F4-C treatment layer: memory may rank, never change the candidate surface.

This module deliberately contains no Factorio authority. It turns the frozen
candidate surface declared by the F4-C protocol into a deterministic ranking
trace. MEMORY_ON may contribute support derived from the frozen F4-B memory
snapshot; MEMORY_ABLATED receives the identical candidates with zero memory
support.

A selected candidate is not automatically executable. Candidate bindings are
declared separately so phase-state can distinguish treatment semantics from
protocol-runner executability.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from factorio_ai_lab.cortex.memory_retrieval import lexical_similarity

TREATMENT_POLICY_VERSION = "cortex_f4c_memory_candidate_ranking_v1"


@dataclass(frozen=True)
class CandidateBinding:
    family: str
    candidate: str
    planner_components: tuple[str, ...]
    treatment_document: str
    planner_bound: bool
    protocol_executable: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["planner_components"] = list(self.planner_components)
        return payload


@dataclass(frozen=True)
class CandidateScore:
    candidate: str
    base_score: float
    memory_support: float
    total_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TreatmentDecision:
    policy_version: str
    candidates: tuple[str, ...]
    scores: tuple[CandidateScore, ...]
    selected: str | None
    memory_influence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "candidates": list(self.candidates),
            "scores": [row.to_dict() for row in self.scores],
            "selected": self.selected,
            "memory_influence": self.memory_influence,
        }


_BINDINGS = (
    CandidateBinding(
        "structural_flow_repair",
        "placement_processing",
        (
            "cortex.structural_prepare.prepare_structural_branch",
            "cortex.structural_execute.StructuralTransactionalAdapter",
        ),
        "place processing processor producer buffer build delivery link",
        True,
    ),
    CandidateBinding(
        "structural_flow_repair",
        "reroute_logistics",
        (
            "learning.repair_loop.propose_actions",
            "planning.rebuild.plan_rebuild",
        ),
        "reroute producer logistics rebuild belt dead output chain route",
        True,
    ),
    CandidateBinding(
        "structural_flow_repair",
        "rebuild_segment",
        ("planning.rebuild.plan_rebuild",),
        "rebuild segment demolish repair broken dead output chain belt",
        True,
    ),
    CandidateBinding(
        "fuel_energy_recovery",
        "local_refuel",
        (
            "learning.repair_loop.plan_repairs",
            "planning.resupply.plan_supply",
        ),
        "local refuel resupply insert fuel world container no fuel boiler",
        True,
    ),
    CandidateBinding(
        "fuel_energy_recovery",
        "route_endogenous_fuel",
        ("planning.resupply.plan_supply",),
        "route endogenous fuel coal self sufficiency supply chain resupply",
        True,
    ),
    CandidateBinding(
        "fuel_energy_recovery",
        "establish_power_generation",
        (
            "planning.progression.DEFAULT_GOALS[steam_power]",
            "learning.repair_loop.INTENT_RESTORE_STEAM_PATH",
        ),
        "establish power generation steam boiler engine power deficit",
        True,
    ),
    CandidateBinding(
        "spatial_logistics_routing",
        "weighted_astar",
        ("planning.astar.weighted_astar",),
        "weighted astar route path obstacles turn penalty spatial logistics",
        True,
    ),
    CandidateBinding(
        "spatial_logistics_routing",
        "detour_with_underground",
        (
            "planning.astar.weighted_astar",
            "planning.runtime_catalog.max_underground_distance",
        ),
        "detour underground route obstacles logistics belt",
        True,
    ),
    CandidateBinding(
        "spatial_logistics_routing",
        "alternate_corridor",
        ("planning.astar.weighted_astar",),
        "alternate corridor routing spatial logistics belt route",
        True,
    ),
    CandidateBinding(
        "production_transition_planning",
        "buffer_first",
        (
            "planning.production_dag.ProductionDagPlanner",
            "planning.dependency_plan.DependencyPlanner",
        ),
        "buffer first inventory margin production transition material",
        True,
    ),
    CandidateBinding(
        "production_transition_planning",
        "power_first",
        (
            "planning.progression.DEFAULT_GOALS[steam_power]",
            "planning.dependency_plan.DependencyPlanner",
        ),
        "power first steam power production transition dependency",
        True,
    ),
    CandidateBinding(
        "production_transition_planning",
        "route_first",
        (
            "planning.delivery.plan_delivery",
            "planning.dependency_plan.DependencyPlanner",
        ),
        "route first logistics transport belt inserter production transition",
        True,
    ),
    CandidateBinding(
        "production_transition_planning",
        "craft_chain_first",
        (
            "planning.production_dag.ProductionDagPlanner",
            "planning.dependency_plan.DependencyPlanner",
        ),
        "craft chain first dependency production science automation",
        True,
    ),
)

CANDIDATE_BINDINGS = {row.candidate: row for row in _BINDINGS}


def task_query_text(task: Mapping[str, Any]) -> str:
    """Build a treatment query from task context, excluding solution labels."""

    family = str(task.get("family") or "")
    spec = task.get("spec")
    if not isinstance(spec, Mapping):
        return family
    parts = [family.replace("_", " ")]
    for key in sorted(spec):
        if key in {"candidate_classes", "hard_postconditions"}:
            continue
        value = spec[key]
        if isinstance(value, (str, int, float, bool)):
            parts.extend((key.replace("_", " "), str(value)))
    return " ".join(parts)


def _memory_document(row: Mapping[str, Any]) -> str:
    key = str(row.get("key") or "")
    content = row.get("content")
    if isinstance(content, Mapping):
        content_text = json.dumps(
            dict(content),
            sort_keys=True,
            ensure_ascii=False,
        )
    else:
        content_text = str(content or "")
    return f"{key} {content_text}".strip()


def _memory_weight(row: Mapping[str, Any]) -> float:
    scores = row.get("scores")
    if isinstance(scores, Mapping):
        total = scores.get("total")
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            return max(0.0, float(total))
    return 1.0


def rank_candidates(
    candidates: Sequence[str],
    retrieval_rows: Iterable[Mapping[str, Any]],
    *,
    base_scores: Mapping[str, float] | None = None,
) -> TreatmentDecision:
    """Rank an unchanged candidate set with optional frozen-memory support."""

    frozen_candidates = tuple(str(value) for value in candidates)
    if not frozen_candidates:
        return TreatmentDecision(
            policy_version=TREATMENT_POLICY_VERSION,
            candidates=(),
            scores=(),
            selected=None,
            memory_influence=0.0,
        )
    if len(set(frozen_candidates)) != len(frozen_candidates):
        raise ValueError("candidate surface must not contain duplicates")
    unknown = [
        name for name in frozen_candidates if name not in CANDIDATE_BINDINGS
    ]
    if unknown:
        raise ValueError("unbound candidate classes: " + ", ".join(unknown))

    rows = tuple(dict(row) for row in retrieval_rows)
    scored: list[CandidateScore] = []
    for index, candidate in enumerate(frozen_candidates):
        binding = CANDIDATE_BINDINGS[candidate]
        base = (
            float(base_scores[candidate])
            if base_scores is not None and candidate in base_scores
            else -float(index) * 1e-9
        )
        support = 0.0
        for row in rows:
            document = _memory_document(row)
            if not document:
                continue
            support += (
                _memory_weight(row)
                * lexical_similarity(binding.treatment_document, document)
            )
        scored.append(
            CandidateScore(
                candidate=candidate,
                base_score=base,
                memory_support=support,
                total_score=base + support,
            )
        )

    selected = max(
        enumerate(scored),
        key=lambda pair: (
            pair[1].total_score,
            -pair[0],
        ),
    )[1].candidate
    return TreatmentDecision(
        policy_version=TREATMENT_POLICY_VERSION,
        candidates=frozen_candidates,
        scores=tuple(scored),
        selected=selected,
        memory_influence=sum(row.memory_support for row in scored),
    )


def binding_summary() -> dict[str, Any]:
    return {
        "policy_version": TREATMENT_POLICY_VERSION,
        "candidate_count": len(CANDIDATE_BINDINGS),
        "planner_bound_count": sum(
            1 for row in CANDIDATE_BINDINGS.values() if row.planner_bound
        ),
        "protocol_executable_count": sum(
            1 for row in CANDIDATE_BINDINGS.values() if row.protocol_executable
        ),
        "bindings": {
            key: value.to_dict()
            for key, value in sorted(CANDIDATE_BINDINGS.items())
        },
    }
