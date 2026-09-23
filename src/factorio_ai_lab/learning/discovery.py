"""Recover, from the recorded history, what the search actually found.

Every generation writes a verdict -- promoted or rejected -- and the verdict
is all anyone reads back. What the generation *found* is in the same record
and was never recovered: the first time a capability appears, a route that
came out shorter than the incumbent's, the moment the bottleneck moved.

The discarded findings are the ones worth surfacing. Measured on
``runs/evolution_history.jsonl``, eleven generations routed more cheaply than
the incumbent's 7.505 and none of them was promoted, so the same improvement
was found and thrown away eleven times without leaving a trace anyone looks
at. A record that only keeps the winners cannot answer what the search
learned, only who won.

Nothing here infers. A generation whose record carries no verdict has unknown
retention, not false retention: reading that silence as "discarded" would
invent a measurement, which is the substitution this project has already paid
for elsewhere.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: A capability named for the first time in the run.
KIND_CAPABILITY = "capability_first_seen"
#: A route cheaper than the incumbent's, whether or not it was kept.
KIND_ROUTE = "route_improvement"
#: The generation where the reported bottleneck changed.
KIND_BOTTLENECK_SHIFT = "bottleneck_shift"

#: `route cost improved 7.505→7.28`, as compare_challenger writes it. The
#: arrow is the unicode one the selection code emits; matching the ASCII
#: `->` as well keeps an older record readable.
_ROUTE_IMPROVEMENT = re.compile(
    r"route cost improved\s*([0-9]*\.?[0-9]+)\s*(?:→|->)\s*([0-9]*\.?[0-9]+)"
)


@dataclass(frozen=True)
class Discovery:
    """One thing a generation found, and whether selection kept it."""

    generation: int
    kind: str
    detail: str
    #: True when the generation carrying the finding was promoted, False when
    #: it was rejected, and None when no verdict was recorded. The three are
    #: distinct facts.
    retained: bool | None
    run_id: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "kind": self.kind,
            "detail": self.detail,
            "retained": self.retained,
            "run_id": self.run_id,
            "evidence": dict(self.evidence),
        }


def _generation(row: Mapping[str, Any]) -> int | None:
    raw = row.get("generation")
    return int(raw) if isinstance(raw, (int, float)) else None


def _retained(row: Mapping[str, Any]) -> bool | None:
    decision = row.get("decision")
    if not isinstance(decision, Mapping):
        return None
    promoted = decision.get("promoted")
    return bool(promoted) if isinstance(promoted, bool) else None


def _improvements(row: Mapping[str, Any]) -> Sequence[str]:
    decision = row.get("decision")
    if not isinstance(decision, Mapping):
        return ()
    raw = decision.get("improvements")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return [str(item) for item in raw]


def _achieved(row: Mapping[str, Any]) -> frozenset[str]:
    progression = row.get("engineering_progression")
    if not isinstance(progression, Mapping):
        return frozenset()
    raw = progression.get("achieved")
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return frozenset()
    return frozenset(str(item) for item in raw)


def discoveries_from_history(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[Discovery, ...]:
    """Read the generation history into the findings it contains.

    Rows are read in the order given, because "first time seen" is defined by
    that order. A capability that disappears and comes back is not a second
    discovery: the run already knew how to build it.
    """
    found: list[Discovery] = []
    seen_capabilities: set[str] = set()
    previous_bottleneck: str | None = None
    bottleneck_started = False

    for row in rows:
        generation = _generation(row)
        if generation is None:
            continue
        run_id = row.get("run_id")
        run_id = str(run_id) if isinstance(run_id, str) else None
        retained = _retained(row)

        for capability in sorted(_achieved(row) - seen_capabilities):
            found.append(
                Discovery(
                    generation=generation,
                    kind=KIND_CAPABILITY,
                    detail=capability,
                    retained=retained,
                    run_id=run_id,
                    evidence={"capability": capability},
                )
            )
        seen_capabilities |= _achieved(row)

        for item in _improvements(row):
            match = _ROUTE_IMPROVEMENT.search(item)
            if match is None:
                continue
            baseline, achieved = float(match.group(1)), float(match.group(2))
            found.append(
                Discovery(
                    generation=generation,
                    kind=KIND_ROUTE,
                    detail=item,
                    retained=retained,
                    run_id=run_id,
                    evidence={
                        "baseline": baseline,
                        "achieved": achieved,
                        "saved": round(baseline - achieved, 6),
                    },
                )
            )

        bottleneck = row.get("bottleneck")
        bottleneck = str(bottleneck) if isinstance(bottleneck, str) else None
        if bottleneck is not None and (
            not bottleneck_started or bottleneck != previous_bottleneck
        ):
            found.append(
                Discovery(
                    generation=generation,
                    kind=KIND_BOTTLENECK_SHIFT,
                    detail=bottleneck,
                    retained=retained,
                    run_id=run_id,
                    evidence={"from": previous_bottleneck, "to": bottleneck},
                )
            )
            bottleneck_started = True
        if bottleneck is not None:
            previous_bottleneck = bottleneck

    return tuple(found)


def summarise(discoveries: Iterable[Discovery]) -> dict[str, Any]:
    """Count the findings, keeping kept, discarded and unknown apart.

    `best_discarded_route` is the single most useful line: the cheapest route
    the search found and did not keep. It is None when nothing was discarded,
    which is a different statement from a saving of zero.
    """
    items = tuple(discoveries)
    by_kind: dict[str, dict[str, int]] = {}
    retained = discarded = unknown = 0

    for item in items:
        bucket = by_kind.setdefault(
            item.kind, {"total": 0, "retained": 0, "discarded": 0, "unknown": 0}
        )
        bucket["total"] += 1
        if item.retained is True:
            retained += 1
            bucket["retained"] += 1
        elif item.retained is False:
            discarded += 1
            bucket["discarded"] += 1
        else:
            unknown += 1
            bucket["unknown"] += 1

    discarded_routes = [
        item
        for item in items
        if item.kind == KIND_ROUTE and item.retained is False
    ]
    best = min(
        discarded_routes,
        key=lambda item: float(item.evidence.get("achieved", float("inf"))),
        default=None,
    )

    return {
        "total": len(items),
        "retained": retained,
        "discarded": discarded,
        "unknown": unknown,
        "by_kind": by_kind,
        "best_discarded_route": (
            None
            if best is None
            else {
                "generation": best.generation,
                "run_id": best.run_id,
                "baseline": best.evidence.get("baseline"),
                "achieved": best.evidence.get("achieved"),
                "saved": best.evidence.get("saved"),
            }
        ),
    }
