from __future__ import annotations

from dataclasses import dataclass
from math import log1p


@dataclass(frozen=True)
class ObjectiveVector:
    throughput: float
    material_cost: float
    area: float
    energy: float
    route_length: float
    failures: int
    deadlocks: int
    milestones: float = 0.0


@dataclass(frozen=True)
class ReferenceScale:
    throughput: float = 16.0
    material_cost: float = 100.0
    area: float = 100.0
    energy: float = 1_000.0
    route_length: float = 100.0


@dataclass(frozen=True)
class ScalarizationWeights:
    throughput: float = 1.00
    material_cost: float = 0.20
    area: float = 0.15
    energy: float = 0.10
    route_length: float = 0.10
    failures: float = 0.50
    deadlocks: float = 1.00
    milestones: float = 0.25


def scalar_score(
    value: ObjectiveVector,
    *,
    scale: ReferenceScale = ReferenceScale(),
    weights: ScalarizationWeights = ScalarizationWeights(),
) -> float:
    """Score auxiliar; a avaliação deve preservar também o vetor e a fronteira de Pareto."""
    if min(scale.throughput, scale.material_cost, scale.area, scale.energy, scale.route_length) <= 0:
        raise ValueError("Todas as escalas de referência devem ser positivas")

    positive = weights.throughput * log1p(max(value.throughput, 0.0) / scale.throughput)
    positive += weights.milestones * max(value.milestones, 0.0)

    penalty = weights.material_cost * log1p(max(value.material_cost, 0.0) / scale.material_cost)
    penalty += weights.area * max(value.area, 0.0) / scale.area
    penalty += weights.energy * max(value.energy, 0.0) / scale.energy
    penalty += weights.route_length * max(value.route_length, 0.0) / scale.route_length
    penalty += weights.failures * max(value.failures, 0)
    penalty += weights.deadlocks * max(value.deadlocks, 0)
    return positive - penalty
