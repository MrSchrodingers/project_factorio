from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from factorio_ai_lab.agents.llm_router import default_free_router


def _budget_key(key: str, budget: int) -> str:
    """Shorten a key to its share, keeping it distinguishable.

    A key carries meaning, so it degrades rather than disappearing; the tail
    is kept because the distinguishing part of these keys is at the end
    (`..._rate_per_s`, `..._output`).
    """
    limit = max(_MIN_KEY_CHARS, min(len(key), budget))
    if len(key) <= limit:
        return key
    head = (limit - 3) // 2
    tail = limit - 3 - head
    return key[:head] + "..." + key[len(key) - tail :]


def _budget_json(value: Any, budget: int, depth: int = 0) -> Any:
    """Shrink a structure to fit a character budget without emptying it.

    The previous compactor dropped every dict or list whose key was not in a
    hardcoded preference list. The contexts the loop actually builds are keyed
    `previous_run`, `champion`, `history` - none of which were listed - so the
    evidence was discarded and the model received `{}`. Measured on the real
    context: a 2-character payload. An advisor with no evidence cannot advise,
    which is why the mutation had been purely stochastic for 15 generations.

    Budgeting instead of listing keeps the shape of whatever it is handed:
    unknown keys survive, lists keep their most recent entries, and long
    strings are cut rather than dropped. Nothing disappears silently.
    """
    if budget <= 0:
        return "<omitted>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        limit = max(24, min(len(value), budget))
        return value if len(value) <= limit else value[: limit - 3] + "..."
    if depth >= 6:
        return "<deep>"

    if isinstance(value, dict):
        if not value:
            return {}
        # Preferred keys go first and get a larger share; the rest still get a
        # share, so a field added later is never invisible.
        ordered = [k for k in _PREFERRED_KEYS if k in value]
        ordered += [k for k in sorted(value) if k not in _PREFERRED_KEYS]
        # How many fit is a budget question, not a fixed number. A constant cap
        # drops fields the budget could have carried, which is the same defect
        # as the preference list it replaced: a rule unrelated to the
        # constraint decides what the model gets to see.
        ordered = ordered[: _fits(budget, len(ordered))]
        if not ordered:
            return {}
        share = max(_MIN_SHARE, budget // len(ordered))
        # Keys were counted but never shrunk, so a context whose keys are long
        # could not be brought under the budget by any number of rounds and
        # the payload fell back to slicing the serialised string, which cuts
        # mid-token and yields JSON the advisor cannot parse.
        return {
            _budget_key(str(key), share): _budget_json(
                value[key], share, depth + 1
            )
            for key in ordered
        }

    if isinstance(value, (list, tuple)):
        rows = list(value)
        if not rows:
            return []
        # Recency decides which rows to lose, but only when the budget forces
        # a loss: rows that fit are kept, and what is kept is always a
        # contiguous tail, never a sample.
        rows = rows[-_fits(budget, len(rows)) :]
        share = max(_MIN_SHARE, budget // len(rows))
        return [_budget_json(item, share, depth + 1) for item in rows]

    return _budget_json(str(value), budget, depth)


#: Fields that carry the most decision weight; they are ordered first, never
#: used to exclude anything.
_PREFERRED_KEYS = (
    "arena",
    "stage",
    "status",
    "detail",
    "next_action",
    "reason",
    "promoted",
    "regressions",
    "improvements",
    "counterexample",
    "metrics",
    "fitness",
    "configuration",
    "champion_configuration",
    "previous_validation_strategy",
    "previous_run",
    "recent_counterexamples",
)

#: Seconds allowed for one advisory call. A generation takes about 17
#: minutes, so a minute of advice is cheap; timing out and silently reverting
#: to random mutation is what was expensive.
_ADVISOR_TIMEOUT_S = 150.0

#: Enough for the schema-constrained answer plus slack. At 220 the model hit
#: `finish_reason: length` before completing the object.
_ADVISOR_MAX_TOKENS = 420

#: Smallest share of the budget worth giving one entry. Below this an entry
#: degrades to an ellipsis and carries no evidence, so the budget is spent on
#: fewer entries instead of on more unreadable ones.
_MIN_SHARE = 48

#: Shortest a key may be cut to and still tell two fields apart.
_MIN_KEY_CHARS = 24


def _fits(budget: int, available: int) -> int:
    """How many entries this budget can carry, at least one."""
    return max(1, min(available, budget // _MIN_SHARE))

#: Characters, not tokens. The local model runs with an 8k context and the
#: prompt and completion share it, so the evidence is held well under that.
_CONTEXT_BUDGET_CHARS = 2600


def _advisor_payload(context: dict[str, Any]) -> str:
    """Serialise the advisor context, shrinking until it fits the budget.

    Returns a payload that still contains evidence: shrinking is by budget, so
    the result degrades in detail rather than collapsing to an empty object.
    """
    budget = _CONTEXT_BUDGET_CHARS
    for _ in range(6):
        compact = _budget_json(context, budget)
        payload = json.dumps(
            compact, sort_keys=True, separators=(",", ":"), default=str
        )
        if len(payload) <= _CONTEXT_BUDGET_CHARS:
            return payload
        budget = int(budget * 0.7)
    # Six rounds did not converge. Slicing the serialised string would cut
    # mid-token and hand the model something it cannot parse, which is worse
    # than handing it less: a payload that fails to parse carries no evidence
    # at all. The last resort is therefore a well-formed envelope that still
    # names what was dropped.
    return json.dumps(
        {
            "truncated": True,
            "reason": "context did not fit the budget after six rounds",
            "keys": sorted(str(key)[:40] for key in context)[:12],
        },
        sort_keys=True,
        separators=(",", ":"),
    )[:_CONTEXT_BUDGET_CHARS]


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
                timeout_s=max(float(provider.timeout_s), _ADVISOR_TIMEOUT_S),
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
            max_tokens=_ADVISOR_MAX_TOKENS,
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
