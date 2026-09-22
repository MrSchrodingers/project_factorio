from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvolutionGenome:
    routing_turn_penalty: float = 0.25
    placement_exploration: float = 2.0
    placement_radius_scale: float = 1.0
    coal_safety_stock: int = 4
    coal_producer_refuel: int = 3
    coal_copper_mining_budget: int = 2
    coal_copper_smelting_budget: int = 2
    coal_survival_budget: int = 6
    buffer_target: int = 12
    rebuild_gain_threshold: float = 0.10
    open_play_wood_target: int = 50
    open_play_stone_target: int = 50
    open_play_coal_target: int = 100
    open_play_iron_target: int = 300
    open_play_copper_target: int = 160
    open_play_wood_radius: int = 24
    autonomy_layout_variant: int = 0
    autonomy_commissioning_coal: int = 14
    autonomy_belt_margin: int = 6
    autonomy_pole_margin: int = 10
    autonomy_route_detour_margin: int = 8

    def to_dict(self) -> dict[str, float | int]:
        return {
            "routing_turn_penalty": round(self.routing_turn_penalty, 6),
            "placement_exploration": round(self.placement_exploration, 6),
            "placement_radius_scale": round(self.placement_radius_scale, 6),
            "coal_safety_stock": self.coal_safety_stock,
            "coal_producer_refuel": self.coal_producer_refuel,
            "coal_copper_mining_budget": self.coal_copper_mining_budget,
            "coal_copper_smelting_budget": self.coal_copper_smelting_budget,
            "coal_survival_budget": self.coal_survival_budget,
            "buffer_target": self.buffer_target,
            "rebuild_gain_threshold": round(self.rebuild_gain_threshold, 6),
            "open_play_wood_target": self.open_play_wood_target,
            "open_play_stone_target": self.open_play_stone_target,
            "open_play_coal_target": self.open_play_coal_target,
            "open_play_iron_target": self.open_play_iron_target,
            "open_play_copper_target": self.open_play_copper_target,
            "open_play_wood_radius": self.open_play_wood_radius,
            "autonomy_layout_variant": self.autonomy_layout_variant,
            "autonomy_commissioning_coal": self.autonomy_commissioning_coal,
            "autonomy_belt_margin": self.autonomy_belt_margin,
            "autonomy_pole_margin": self.autonomy_pole_margin,
            "autonomy_route_detour_margin": self.autonomy_route_detour_margin,
        }


def _float(
    config: Mapping[str, Any],
    key: str,
    default: float,
) -> float:
    raw = config.get(key, default)
    return float(raw) if isinstance(raw, (int, float)) else default


def _int(
    config: Mapping[str, Any],
    key: str,
    default: int,
) -> int:
    raw = config.get(key, default)
    return int(raw) if isinstance(raw, (int, float)) else default


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _clip_int(value: int, lower: int, upper: int) -> int:
    return min(upper, max(lower, value))


def challenger_genome(
    *,
    attempt: int,
    champion_configuration: Mapping[str, Any] | None = None,
    default_turn_penalty: float = 0.25,
    default_exploration: float = 2.0,
    seed: int = 20260921,
) -> EvolutionGenome:
    """Mutate a reproducible engineering genome around the incumbent.

    Attempt 1 is a control. Later attempts apply deterministic pseudo-random
    mutations so rejected generations explore new routing, spatial and material
    allocation policies while remaining reproducible.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")

    config = champion_configuration or {}
    base = EvolutionGenome(
        routing_turn_penalty=_float(
            config,
            "routing_turn_penalty",
            default_turn_penalty,
        ),
        placement_exploration=_float(
            config,
            "placement_exploration",
            _float(config, "ucb_exploration", default_exploration),
        ),
        placement_radius_scale=_float(config, "placement_radius_scale", 1.0),
        coal_safety_stock=_int(config, "coal_safety_stock", 4),
        coal_producer_refuel=_int(config, "coal_producer_refuel", 3),
        coal_copper_mining_budget=_int(
            config,
            "coal_copper_mining_budget",
            2,
        ),
        coal_copper_smelting_budget=_int(
            config,
            "coal_copper_smelting_budget",
            2,
        ),
        coal_survival_budget=_int(config, "coal_survival_budget", 6),
        buffer_target=_int(config, "buffer_target", 12),
        rebuild_gain_threshold=_float(
            config,
            "rebuild_gain_threshold",
            0.10,
        ),
        open_play_wood_target=_int(config, "open_play_wood_target", 50),
        open_play_stone_target=_int(config, "open_play_stone_target", 50),
        open_play_coal_target=_int(config, "open_play_coal_target", 100),
        open_play_iron_target=_int(config, "open_play_iron_target", 300),
        open_play_copper_target=_int(config, "open_play_copper_target", 160),
        open_play_wood_radius=_int(config, "open_play_wood_radius", 24),
        autonomy_layout_variant=_int(
            config,
            "autonomy_layout_variant",
            0,
        ),
        autonomy_commissioning_coal=_int(
            config,
            "autonomy_commissioning_coal",
            14,
        ),
        autonomy_belt_margin=_int(
            config,
            "autonomy_belt_margin",
            6,
        ),
        autonomy_pole_margin=_int(
            config,
            "autonomy_pole_margin",
            10,
        ),
        autonomy_route_detour_margin=_int(
            config,
            "autonomy_route_detour_margin",
            8,
        ),
    )
    if attempt == 1:
        return base

    rng = random.Random((seed * 1_000_003) ^ (attempt * 97_409))

    def log_mutate(value: float, sigma: float) -> float:
        return value * math.exp(rng.gauss(0.0, sigma))

    return EvolutionGenome(
        routing_turn_penalty=_clip(
            log_mutate(base.routing_turn_penalty, 0.30),
            0.02,
            2.0,
        ),
        placement_exploration=_clip(
            log_mutate(base.placement_exploration, 0.25),
            0.15,
            4.0,
        ),
        placement_radius_scale=_clip(
            log_mutate(base.placement_radius_scale, 0.18),
            0.65,
            1.75,
        ),
        coal_safety_stock=_clip_int(
            base.coal_safety_stock + rng.choice((-2, -1, 0, 1, 2)),
            2,
            12,
        ),
        coal_producer_refuel=_clip_int(
            base.coal_producer_refuel + rng.choice((-1, 0, 1, 2)),
            1,
            6,
        ),
        coal_copper_mining_budget=_clip_int(
            base.coal_copper_mining_budget + rng.choice((-1, 0, 1)),
            1,
            5,
        ),
        coal_copper_smelting_budget=_clip_int(
            base.coal_copper_smelting_budget + rng.choice((-1, 0, 1)),
            1,
            5,
        ),
        coal_survival_budget=_clip_int(
            base.coal_survival_budget + rng.choice((-2, -1, 0, 1, 2)),
            5,
            12,
        ),
        buffer_target=_clip_int(
            base.buffer_target + rng.choice((-4, -2, 0, 2, 4, 6)),
            6,
            40,
        ),
        rebuild_gain_threshold=_clip(
            base.rebuild_gain_threshold + rng.gauss(0.0, 0.025),
            0.03,
            0.30,
        ),
        open_play_wood_target=_clip_int(
            base.open_play_wood_target + rng.choice((-10, 0, 10, 20)),
            30,
            120,
        ),
        open_play_stone_target=_clip_int(
            base.open_play_stone_target + rng.choice((-10, 0, 10, 20)),
            30,
            120,
        ),
        open_play_coal_target=_clip_int(
            base.open_play_coal_target + rng.choice((-20, 0, 20, 40)),
            60,
            220,
        ),
        open_play_iron_target=_clip_int(
            base.open_play_iron_target + rng.choice((-40, 0, 40, 80)),
            260,
            520,
        ),
        open_play_copper_target=_clip_int(
            base.open_play_copper_target + rng.choice((-20, 0, 20, 40)),
            120,
            300,
        ),
        open_play_wood_radius=_clip_int(
            base.open_play_wood_radius + rng.choice((-4, 0, 4, 8)),
            12,
            48,
        ),
        autonomy_layout_variant=(
            base.autonomy_layout_variant
            + rng.choice((0, 0, 1, 2, 3))
        )
        % 4,
        autonomy_commissioning_coal=_clip_int(
            base.autonomy_commissioning_coal + rng.choice((-2, 0, 2, 4)),
            8,
            32,
        ),
        autonomy_belt_margin=_clip_int(
            base.autonomy_belt_margin + rng.choice((-2, 0, 2, 4)),
            2,
            24,
        ),
        autonomy_pole_margin=_clip_int(
            base.autonomy_pole_margin + rng.choice((-2, 0, 2, 4)),
            4,
            36,
        ),
        autonomy_route_detour_margin=_clip_int(
            base.autonomy_route_detour_margin + rng.choice((-2, 0, 2, 4)),
            2,
            24,
        ),
    )


def apply_advice(
    genome: EvolutionGenome,
    adjustments: Mapping[str, str] | list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> EvolutionGenome:
    """Apply bounded LLM mutation directions to an already reproducible genome."""
    if isinstance(adjustments, Mapping):
        rows = list(adjustments.items())
    else:
        rows = list(adjustments)
    values = genome.to_dict()
    steps: dict[str, float | int] = {
        "routing_turn_penalty": 0.04,
        "placement_exploration": 0.20,
        "placement_radius_scale": 0.08,
        "coal_safety_stock": 1,
        "coal_producer_refuel": 1,
        "coal_copper_mining_budget": 1,
        "coal_copper_smelting_budget": 1,
        "coal_survival_budget": 1,
        "buffer_target": 2,
        "rebuild_gain_threshold": 0.02,
        "open_play_wood_target": 10,
        "open_play_stone_target": 10,
        "open_play_coal_target": 20,
        "open_play_iron_target": 40,
        "open_play_copper_target": 20,
        "open_play_wood_radius": 4,
        "autonomy_layout_variant": 1,
        "autonomy_commissioning_coal": 2,
        "autonomy_belt_margin": 2,
        "autonomy_pole_margin": 2,
        "autonomy_route_detour_margin": 2,
    }
    for parameter, direction in rows[:3]:
        if parameter not in steps or direction == "hold":
            continue
        sign = 1 if direction == "increase" else -1
        values[parameter] = values[parameter] + sign * steps[parameter]

    return EvolutionGenome(
        routing_turn_penalty=_clip(float(values["routing_turn_penalty"]), 0.02, 2.0),
        placement_exploration=_clip(float(values["placement_exploration"]), 0.15, 4.0),
        placement_radius_scale=_clip(float(values["placement_radius_scale"]), 0.65, 1.75),
        coal_safety_stock=_clip_int(int(values["coal_safety_stock"]), 2, 12),
        coal_producer_refuel=_clip_int(int(values["coal_producer_refuel"]), 1, 6),
        coal_copper_mining_budget=_clip_int(int(values["coal_copper_mining_budget"]), 1, 5),
        coal_copper_smelting_budget=_clip_int(int(values["coal_copper_smelting_budget"]), 1, 5),
        coal_survival_budget=_clip_int(int(values["coal_survival_budget"]), 5, 12),
        buffer_target=_clip_int(int(values["buffer_target"]), 6, 40),
        rebuild_gain_threshold=_clip(float(values["rebuild_gain_threshold"]), 0.03, 0.30),
        open_play_wood_target=_clip_int(int(values["open_play_wood_target"]), 30, 120),
        open_play_stone_target=_clip_int(int(values["open_play_stone_target"]), 30, 120),
        open_play_coal_target=_clip_int(int(values["open_play_coal_target"]), 60, 220),
        open_play_iron_target=_clip_int(int(values["open_play_iron_target"]), 260, 520),
        open_play_copper_target=_clip_int(int(values["open_play_copper_target"]), 120, 300),
        open_play_wood_radius=_clip_int(int(values["open_play_wood_radius"]), 12, 48),
        autonomy_layout_variant=int(values["autonomy_layout_variant"]) % 4,
        autonomy_commissioning_coal=_clip_int(
            int(values["autonomy_commissioning_coal"]),
            8,
            32,
        ),
        autonomy_belt_margin=_clip_int(
            int(values["autonomy_belt_margin"]),
            2,
            24,
        ),
        autonomy_pole_margin=_clip_int(
            int(values["autonomy_pole_margin"]),
            4,
            36,
        ),
        autonomy_route_detour_margin=_clip_int(
            int(values["autonomy_route_detour_margin"]),
            2,
            24,
        ),
    )
