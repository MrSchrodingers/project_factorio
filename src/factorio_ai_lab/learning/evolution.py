from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvolutionGenome:
    routing_turn_penalty: float = 0.25
    placement_exploration: float = 2.0

    def to_dict(self) -> dict[str, float]:
        return {
            "routing_turn_penalty": round(self.routing_turn_penalty, 6),
            "placement_exploration": round(self.placement_exploration, 6),
        }


_MUTATION_SCHEDULE: tuple[tuple[float, float], ...] = (
    (1.00, 1.00),  # control challenger
    (0.60, 0.75),  # straighter/less exploratory
    (1.40, 1.25),  # more turn tolerance / exploration
    (0.40, 1.15),
    (1.80, 0.85),
    (0.80, 1.45),
)


def challenger_genome(
    *,
    attempt: int,
    champion_configuration: Mapping[str, Any] | None = None,
    default_turn_penalty: float = 0.25,
    default_exploration: float = 2.0,
) -> EvolutionGenome:
    """Create a deterministic challenger around the incumbent genome."""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")

    champion_configuration = champion_configuration or {}
    base_turn = float(
        champion_configuration.get(
            "routing_turn_penalty",
            default_turn_penalty,
        )
        or default_turn_penalty
    )
    base_exploration = float(
        champion_configuration.get(
            "placement_exploration",
            champion_configuration.get(
                "ucb_exploration",
                default_exploration,
            ),
        )
        or default_exploration
    )

    turn_factor, exploration_factor = _MUTATION_SCHEDULE[
        (attempt - 1) % len(_MUTATION_SCHEDULE)
    ]
    turn = min(2.0, max(0.0, base_turn * turn_factor))
    exploration = min(4.0, max(0.15, base_exploration * exploration_factor))
    return EvolutionGenome(
        routing_turn_penalty=turn,
        placement_exploration=exploration,
    )
