from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskKind(StrEnum):
    DECOMPOSE_GOAL = "decompose_goal"
    SYNTHESIZE_CODE = "synthesize_code"
    CRITIQUE = "critique"
    SUMMARIZE = "summarize"
    SPATIAL_ROUTE = "spatial_route"
    PRODUCTION_PLAN = "production_plan"


class Engine(StrEnum):
    LLM = "llm"
    ASTAR = "astar"
    OPTIMIZER = "optimizer"
    DETERMINISTIC = "deterministic"


@dataclass(frozen=True)
class RouteDecision:
    engine: Engine
    reason: str


def route_task(task: TaskKind) -> RouteDecision:
    """Router explícito e auditável; uma versão aprendida deve superar este baseline."""
    if task is TaskKind.SPATIAL_ROUTE:
        return RouteDecision(Engine.ASTAR, "Geometria discreta é resolvida sem LLM.")
    if task is TaskKind.PRODUCTION_PLAN:
        return RouteDecision(Engine.OPTIMIZER, "Fluxos e capacidades formam problema algébrico.")
    if task in {TaskKind.DECOMPOSE_GOAL, TaskKind.SYNTHESIZE_CODE, TaskKind.CRITIQUE}:
        return RouteDecision(Engine.LLM, "Tarefa semântica/programática de alto nível.")
    return RouteDecision(Engine.DETERMINISTIC, "Pipeline estruturado é suficiente.")
