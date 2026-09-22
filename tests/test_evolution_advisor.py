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


def test_advisor_payload_keeps_recent_counterexamples_only():
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
    assert len(rows) == 8
    assert rows[0]["stage"] == "stage-4"
    assert rows[-1]["stage"] == "stage-11"
