from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from factorio_ai_lab.agents.llm_router import default_free_router

PARAMETERS = (
    "routing_turn_penalty",
    "placement_exploration",
    "placement_radius_scale",
    "coal_safety_stock",
    "coal_producer_refuel",
    "coal_copper_mining_budget",
    "coal_copper_smelting_budget",
    "coal_survival_budget",
    "buffer_target",
    "rebuild_gain_threshold",
    "open_play_wood_target",
    "open_play_stone_target",
    "open_play_coal_target",
    "open_play_iron_target",
    "open_play_copper_target",
    "open_play_wood_radius",
    "autonomy_layout_variant",
    "autonomy_commissioning_coal",
    "autonomy_belt_margin",
    "autonomy_pole_margin",
    "autonomy_route_detour_margin",
)


@dataclass(frozen=True)
class EvolutionAdvice:
    hypothesis: str
    adjustments: tuple[tuple[str, str], ...]
    provider: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "adjustments": [
                {"parameter": parameter, "direction": direction}
                for parameter, direction in self.adjustments
            ],
            "provider": self.provider,
        }


def propose_evolution_advice(context: dict[str, Any]) -> EvolutionAdvice:
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "factorio_evolution_advice",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "adjustments": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {
                            "type": "object",
                            "properties": {
                                "parameter": {
                                    "type": "string",
                                    "enum": list(PARAMETERS),
                                },
                                "direction": {
                                    "type": "string",
                                    "enum": ["increase", "decrease", "hold"],
                                },
                            },
                            "required": ["parameter", "direction"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["hypothesis", "adjustments"],
                "additionalProperties": False,
            },
        },
    }
    try:
        base_router = default_free_router()
        bounded_router = type(base_router)(
            replace(
                provider,
                timeout_s=min(float(provider.timeout_s), 15.0),
            )
            for provider in base_router.providers
        )
        result = bounded_router.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an engineering evolution advisor for Factorio. "
                        "Use only the measured counterexample and incumbent data. "
                        "Choose at most three bounded parameter directions. "
                        "Do not invent measurements or claim success."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(context, sort_keys=True, default=str),
                },
            ],
            temperature=0.1,
            max_tokens=220,
            response_format=schema,
        )
        parsed = json.loads(result["choices"][0]["message"]["content"])
        adjustments = tuple(
            (row["parameter"], row["direction"])
            for row in parsed["adjustments"]
            if row["parameter"] in PARAMETERS
        )
        return EvolutionAdvice(
            hypothesis=str(parsed["hypothesis"]),
            adjustments=adjustments,
            provider=result.get("_router"),
        )
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return EvolutionAdvice(
            hypothesis="No verified LLM mutation advice; use reproducible stochastic mutation.",
            adjustments=(),
            provider={"error": f"{type(exc).__name__}: {exc}"},
        )
