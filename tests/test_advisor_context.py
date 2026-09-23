"""The advisor must actually receive the evidence it is meant to reason over.

The previous compactor kept a dict or list only when its key appeared in a
hardcoded preference list. The contexts the loop builds are keyed
`previous_run`, `champion` and `history`, none of which were listed, so every
one of them was dropped and the model received `{}` -- a 2-character payload,
measured on the real context. An advisor with no evidence cannot advise, which
is why fifteen consecutive generations recorded no adjustment and the mutation
was purely stochastic.

Budgeting instead of listing is the fix: unknown keys survive, lists keep
their most recent entries, long strings are cut rather than dropped, and the
payload degrades in detail instead of collapsing.
"""

from __future__ import annotations

import json

from factorio_ai_lab.agents.evolution_advisor import _advisor_payload

REAL_SHAPE = {
    "champion_configuration": {"routing_turn_penalty": 0.505, "buffer_target": 12},
    "previous_run": {
        "run_id": "curriculum-20260923T012021Z",
        "arena": "lab_play",
        "status": "partial_success",
        "stage": "Electronic circuits",
        "detail": "cable transfer produced nothing",
        "metrics": {"copper_ore_output": 6.0, "electronic_circuit_output": 0.0},
    },
}


def test_evidence_is_not_dropped_for_being_unlisted() -> None:
    payload = json.loads(_advisor_payload(REAL_SHAPE))
    assert "previous_run" in payload, "a evidencia da geracao anterior sumiu"
    assert payload["previous_run"]["stage"] == "Electronic circuits"
    assert payload["previous_run"]["metrics"], "as metricas sumiram"


def test_a_key_nobody_anticipated_still_survives() -> None:
    # The failure mode was structural: any field added later was invisible.
    payload = json.loads(
        _advisor_payload({"a_field_added_next_month": {"value": 42}})
    )
    assert payload.get("a_field_added_next_month", {}).get("value") == 42


def test_payload_is_never_empty_when_context_is_not() -> None:
    for context in (
        REAL_SHAPE,
        {"champion": {"generation": 6}},
        {"history": [{"generation": 30}, {"generation": 31}]},
        {"counterexample": {"stage": "circuits", "metrics": {"cable": 0.0}}},
    ):
        payload = _advisor_payload(context)
        assert payload not in ("{}", "[]", ""), f"payload vazio para {context}"
        assert len(payload) > 10


def test_an_empty_context_stays_empty() -> None:
    # Nothing in must not become something out.
    assert _advisor_payload({}) == "{}"


def test_a_huge_context_is_shrunk_but_still_carries_evidence() -> None:
    huge = {
        "champion_configuration": {f"param_{i}": i for i in range(200)},
        "previous_run": {
            "stage": "Electronic circuits",
            "metrics": {f"metric_{i}": float(i) for i in range(400)},
        },
        "history": [{"generation": i, "payload": "x" * 400} for i in range(120)],
    }
    payload = _advisor_payload(huge)
    assert len(payload) <= 5200, f"estourou o orcamento: {len(payload)}"
    decoded = json.loads(payload)
    # Shrunk, not emptied: the decisive field is still identifiable.
    assert "previous_run" in decoded
    assert decoded["previous_run"].get("stage")


def test_lists_keep_the_most_recent_entries() -> None:
    payload = json.loads(
        _advisor_payload({"history": [{"generation": i} for i in range(1, 41)]})
    )
    generations = [row.get("generation") for row in payload["history"]]
    assert generations, "a lista inteira sumiu"
    # Recency decides the next mutation; the oldest rows are the ones to lose.
    assert max(g for g in generations if isinstance(g, int)) == 40


def test_the_payload_is_valid_json() -> None:
    json.loads(_advisor_payload(REAL_SHAPE))
