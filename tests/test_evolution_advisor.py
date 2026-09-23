import json

from factorio_ai_lab.agents.evolution_advisor import _advisor_payload


def test_advisor_payload_bounds_large_context_for_local_model():
    context = {
        "champion_configuration": {"autonomy_belt_margin": 4},
        "counterexample": {
            "stage": "Green-science industry",
            "detail": "failure " + "x" * 20000,
        },
        "recent_counterexamples": [
            {"stage": f"stage-{index}", "detail": "y" * 4000}
            for index in range(30)
        ],
        "telemetry_dump": {
            f"field-{index}": "z" * 5000
            for index in range(100)
        },
    }

    payload = _advisor_payload(context)
    decoded = json.loads(payload)

    assert len(payload) <= 6200
    assert decoded["champion_configuration"]["autonomy_belt_margin"] == 4
    assert decoded["counterexample"]["stage"] == "Green-science industry"


def test_short_rows_that_fit_the_budget_are_all_kept():
    # Cutting a list to a fixed length discards evidence the budget could
    # have carried. That is the same defect as dropping a key for not being
    # on a preference list: a rule unrelated to the constraint decides what
    # the model gets to see.
    payload = _advisor_payload(
        {
            "recent_counterexamples": [
                {"stage": f"stage-{index}"}
                for index in range(12)
            ]
        }
    )
    decoded = json.loads(payload)

    rows = decoded["recent_counterexamples"]
    assert len(rows) == 12, f"descartou linhas que cabiam: {len(rows)}"
    assert rows[-1]["stage"] == "stage-11"


def test_rows_that_do_not_fit_lose_the_oldest_first():
    # When the budget genuinely cannot hold everything, recency decides: the
    # rows kept must be a contiguous tail, never a sample.
    payload = _advisor_payload(
        {
            "recent_counterexamples": [
                {"stage": f"stage-{index}", "detail": "y" * 900}
                for index in range(200)
            ]
        }
    )
    decoded = json.loads(payload)

    rows = decoded["recent_counterexamples"]
    assert 0 < len(rows) < 200, f"nao encolheu: {len(rows)}"
    kept = [row["stage"] for row in rows]
    expected = [f"stage-{index}" for index in range(200 - len(rows), 200)]
    assert kept == expected, f"nao e o sufixo mais recente: {kept}"


def test_keys_that_fit_the_budget_are_all_kept():
    context = {f"field-{index:02d}": index for index in range(28)}
    decoded = json.loads(_advisor_payload(context))
    assert len(decoded) == 28, f"descartou chaves que cabiam: {len(decoded)}"


def test_the_payload_is_valid_json_even_when_a_key_is_enormous():
    # Keys were counted but never truncated, so six shrink rounds could not
    # converge and the function fell back to slicing the string, which cuts
    # mid-token. The old compactor always emitted a well-formed envelope; a
    # malformed payload is a regression in failure mode, and the advisor
    # answers with nothing when the payload will not parse.
    for width in (50, 200, 500, 2000):
        context = {
            f"field-{index}" + "K" * width: {"stage": f"stage-{index}"}
            for index in range(20)
        }
        payload = _advisor_payload(context)
        json.loads(payload)  # raises if the envelope is broken


def test_an_enormous_key_still_carries_its_value():
    # Truncating must degrade the key, not delete the evidence under it.
    context = {
        f"field-{index}" + "K" * 900: {"stage": f"stage-{index}"}
        for index in range(20)
    }
    decoded = json.loads(_advisor_payload(context))
    assert decoded, "o payload ficou vazio"
    carried = json.dumps(decoded)
    assert "stage-" in carried, f"a evidencia sumiu: {carried[:200]}"
