from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)
_RATE_WORDS = re.compile(
    r"(?i)(?:per\s+second|/s\b|items?/s\b|per\s+minute|/min\b|items?/min\b)"
)


@dataclass(frozen=True)
class KnowledgeVerification:
    verified: bool
    unsupported_numbers: tuple[float, ...] = ()
    unknown_evidence_keys: tuple[str, ...] = ()
    rate_claim_without_rate_fact: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "unsupported_numbers": list(self.unsupported_numbers),
            "unknown_evidence_keys": list(self.unknown_evidence_keys),
            "rate_claim_without_rate_fact": self.rate_claim_without_rate_fact,
        }


def flatten_numeric_facts(
    facts: Mapping[str, Any],
    *,
    prefix: str = "",
) -> dict[str, float]:
    flattened: dict[str, float] = {}
    for key, value in facts.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            flattened[name] = float(value)
        elif isinstance(value, Mapping):
            flattened.update(flatten_numeric_facts(value, prefix=name))
    return flattened


def _number_supported(value: float, candidates: tuple[float, ...]) -> bool:
    for candidate in candidates:
        tolerance = max(1e-6, abs(candidate) * 0.015)
        if abs(value - candidate) <= tolerance:
            return True
        if abs(candidate) > 0 and abs(value - round(candidate, 1)) <= 1e-6:
            return True
        if abs(candidate) > 0 and abs(value - round(candidate, 2)) <= 1e-6:
            return True
    return False


def verify_generated_knowledge(
    *,
    lesson: str,
    hypothesis: str,
    facts: Mapping[str, Any],
    evidence_keys: list[str] | tuple[str, ...],
) -> KnowledgeVerification:
    numeric = flatten_numeric_facts(facts)
    candidates = tuple(numeric.values())
    unknown = tuple(sorted(key for key in evidence_keys if key not in facts))

    unsupported: list[float] = []
    for text in (lesson, hypothesis):
        for match in _NUMBER_RE.finditer(text):
            value = float(match.group(1))
            suffix = text[match.end() : match.end() + 1]
            if suffix == "%":
                value /= 100.0
            if not _number_supported(value, candidates):
                unsupported.append(float(match.group(1)))

    has_rate_fact = any(
        "rate" in key.lower()
        or key.lower().endswith("_per_s")
        or key.lower().endswith("_per_min")
        for key in numeric
    )
    rate_claim_without_rate_fact = bool(
        _RATE_WORDS.search(lesson + " " + hypothesis)
    ) and not has_rate_fact

    verified = not unsupported and not unknown and not rate_claim_without_rate_fact
    return KnowledgeVerification(
        verified=verified,
        unsupported_numbers=tuple(unsupported),
        unknown_evidence_keys=unknown,
        rate_claim_without_rate_fact=rate_claim_without_rate_fact,
    )
