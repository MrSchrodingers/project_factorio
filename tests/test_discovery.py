"""Evolution has to leave a readable trace, not just a fitness number.

The history records what each generation achieved, but nothing reads it back:
a capability first appears, a route gets shorter, a bottleneck moves, and the
only artefact is a promoted/rejected verdict. What the run actually found is
recoverable from the record and was never recovered.

The discarded findings matter more than the retained ones here. Measured on
runs/evolution_history.jsonl, eleven generations found a cheaper route than
the incumbent's 7.505 and none of them was promoted, so the improvement was
discovered and thrown away eleven times over.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from factorio_ai_lab.learning.discovery import (
    KIND_BOTTLENECK_SHIFT,
    KIND_CAPABILITY,
    KIND_ROUTE,
    discoveries_from_history,
    summarise,
)

HISTORY = Path(__file__).resolve().parents[1] / "runs" / "evolution_history.jsonl"


def _row(generation, *, achieved=(), route=None, bottleneck=None, promoted=False):
    decision = {"promoted": promoted, "improvements": [], "regressions": []}
    if route is not None:
        decision["improvements"].append(f"route cost improved 7.505→{route}")
    return {
        "generation": generation,
        "run_id": f"run-{generation}",
        "decision": decision,
        "engineering_progression": {"achieved": list(achieved)},
        "bottleneck": bottleneck,
    }


def test_a_capability_is_recorded_the_first_time_it_appears() -> None:
    rows = [
        _row(1, achieved=["iron_backbone"]),
        _row(2, achieved=["iron_backbone", "copper_mining"]),
        _row(3, achieved=["iron_backbone", "copper_mining"]),
    ]
    found = [d for d in discoveries_from_history(rows) if d.kind == KIND_CAPABILITY]
    assert [(d.generation, d.detail) for d in found] == [
        (1, "iron_backbone"),
        (2, "copper_mining"),
    ]


def test_a_capability_that_is_lost_and_returns_is_not_a_new_discovery() -> None:
    rows = [
        _row(1, achieved=["copper_mining"]),
        _row(2, achieved=[]),
        _row(3, achieved=["copper_mining"]),
    ]
    found = [d for d in discoveries_from_history(rows) if d.kind == KIND_CAPABILITY]
    assert len(found) == 1, f"creditou reaquisicao como descoberta: {found}"


def test_a_route_improvement_is_recorded_with_the_measured_values() -> None:
    rows = [_row(8, route=7.357)]
    found = [d for d in discoveries_from_history(rows) if d.kind == KIND_ROUTE]
    assert len(found) == 1
    assert found[0].evidence["baseline"] == pytest.approx(7.505)
    assert found[0].evidence["achieved"] == pytest.approx(7.357)


def test_a_discovery_from_a_rejected_generation_is_marked_discarded() -> None:
    rows = [_row(8, route=7.357, promoted=False)]
    found = discoveries_from_history(rows)
    assert found[0].retained is False


def test_retention_is_unknown_when_the_verdict_was_not_recorded() -> None:
    # A record written before the verdict existed says nothing about it.
    # Reading that silence as "discarded" invents a measurement.
    row = _row(8, route=7.357)
    del row["decision"]["promoted"]
    found = discoveries_from_history([row])
    assert found[0].retained is None


def test_a_bottleneck_shift_is_recorded_once_per_move() -> None:
    rows = [
        _row(12, bottleneck="A* belt logistics"),
        _row(13, bottleneck="Electronic circuits"),
        _row(14, bottleneck="Electronic circuits"),
        _row(15, bottleneck="Logistic science"),
    ]
    found = [
        d for d in discoveries_from_history(rows) if d.kind == KIND_BOTTLENECK_SHIFT
    ]
    assert [d.generation for d in found] == [12, 13, 15]


def test_an_absent_bottleneck_does_not_count_as_a_shift() -> None:
    rows = [_row(1, bottleneck=None), _row(2, bottleneck=None)]
    found = [
        d for d in discoveries_from_history(rows) if d.kind == KIND_BOTTLENECK_SHIFT
    ]
    assert found == []


def test_the_summary_separates_kept_from_discarded() -> None:
    rows = [
        _row(5, achieved=["iron_backbone"], promoted=True),
        _row(8, route=7.357, promoted=False),
        _row(9, route=7.4, promoted=False),
    ]
    report = summarise(discoveries_from_history(rows))
    assert report["total"] == 3
    assert report["discarded"] == 2
    assert report["retained"] == 1
    assert report["by_kind"][KIND_ROUTE]["discarded"] == 2


def test_the_best_discarded_route_is_reported() -> None:
    # The single most useful line of the record: the best thing the search
    # found and did not keep.
    rows = [_row(21, route=7.252), _row(27, route=7.279), _row(8, route=7.357)]
    report = summarise(discoveries_from_history(rows))
    best = report["best_discarded_route"]
    assert best["generation"] == 21
    assert best["achieved"] == pytest.approx(7.252)


def test_an_empty_history_reports_emptiness_not_zeroes() -> None:
    report = summarise(discoveries_from_history([]))
    assert report["total"] == 0
    assert report["best_discarded_route"] is None


@pytest.mark.skipif(not HISTORY.exists(), reason="sem historico gravado")
def test_the_real_history_yields_the_known_discarded_route_improvements() -> None:
    rows = [
        json.loads(line)
        for line in HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = summarise(discoveries_from_history(rows))
    route = report["by_kind"].get(KIND_ROUTE)
    assert route, "o instrumento nao achou o caso positivo conhecido"
    assert route["discarded"] >= 10, (
        f"esperava ao menos 10 melhorias de rota descartadas, achou {route}"
    )
    best = report["best_discarded_route"]
    assert best is not None
    assert best["achieved"] < 7.505, best


def test_a_route_that_was_kept_is_not_reported_as_discarded() -> None:
    # The cheapest route of the run belongs to a promoted generation, so it
    # was not lost. Reporting it as discarded would invent a loss.
    rows = [_row(21, route=7.252, promoted=True), _row(8, route=7.357)]
    best = summarise(discoveries_from_history(rows))["best_discarded_route"]
    assert best["generation"] == 8, f"contou uma descoberta retida como perdida: {best}"


def test_unknown_retention_is_counted_apart_from_discarded() -> None:
    # Three distinct facts, three counters. Folding unknown into discarded
    # would report a loss that was never observed.
    kept = _row(5, route=7.3, promoted=True)
    lost = _row(6, route=7.4, promoted=False)
    silent = _row(7, route=7.45)
    del silent["decision"]["promoted"]

    report = summarise(discoveries_from_history([kept, lost, silent]))
    assert (report["retained"], report["discarded"], report["unknown"]) == (1, 1, 1)
    assert report["by_kind"][KIND_ROUTE]["unknown"] == 1
