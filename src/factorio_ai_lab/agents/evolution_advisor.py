from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from factorio_ai_lab.agents.llm_router import default_free_router


def _compact_llm_context(value: Any, depth: int = 0) -> Any:
    """Bound advisor context so local 8k models receive only decision evidence."""
    if depth >= 4:
        if isinstance(value, (dict, list, tuple)):
            return "<truncated>"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 480 else value[:477] + "..."
    if isinstance(value, dict):
        preferred = (
            "run_id",
            "status",
            "stage",
            "detail",
            "next_action",
            "phase",
            "reason",
            "promoted",
            "regressions",
            "improvements",
            "configuration",
            "fitness",
            "metrics",
            "counterexample",
            "recent_counterexamples",
            "candidate_before_advice",
            "previous_validation_strategy",
            "champion_configuration",
        )
        ordered: list[tuple[str, Any]] = []
        seen: set[str] = set()
        for key in preferred:
            if key in value:
                ordered.append((key, value[key]))
                seen.add(key)
        for key in sorted(value):
            if key in seen:
                continue
            item = value[key]
            if isinstance(item, (str, int, float, bool, type(None))):
                ordered.append((str(key), item))
            if len(ordered) >= 24:
                break
        return {
            str(key): _compact_llm_context(item, depth + 1)
            for key, item in ordered[:24]
        }
    if isinstance(value, (list, tuple)):
        rows = list(value)
        if len(rows) > 8:
            rows = rows[-8:]
        return [_compact_llm_context(item, depth + 1) for item in rows]
    return str(value)[:480]


def _advisor_payload(context: dict[str, Any]) -> str:
    compact = _compact_llm_context(context)
    payload = json.dumps(compact, sort_keys=True, separators=(",", ":"), default=str)
    if len(payload) <= 6200:
        return payload
    # Last-resort deterministic envelope keeps the most relevant decision fields.
    fallback = {
        "champion_configuration": compact.get("champion_configuration")
        if isinstance(compact, dict)
        else None,
        "previous_validation_strategy": compact.get("previous_validation_strategy")
        if isinstance(compact, dict)
        else None,
        "counterexample": compact.get("counterexample")
        if isinstance(compact, dict)
        else None,
        "candidate_before_advice": compact.get("candidate_before_advice")
        if isinstance(compact, dict)
        else None,
    }
    return json.dumps(fallback, sort_keys=True, separators=(",", ":"), default=str)


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
                    "content": _advisor_payload(context),
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
