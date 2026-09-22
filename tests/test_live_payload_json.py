"""The live dashboard payload must be RFC 8259 JSON.

Starlette serialises ``WebSocket.send_json`` with the stdlib default
(``allow_nan=True``), which emits the bare tokens ``Infinity``/``NaN``.
``JSON.parse`` rejects them, so one infinite route cost silences every
live update in the browser. These tests pin the browser-side contract.
"""

from __future__ import annotations

import json

import pytest

from factorio_ai_lab.dashboard.state import json_finite


def _serialise_like_starlette(payload: object) -> str:
    """Byte-for-byte the call in starlette/websockets.py::send_json."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _parse_like_browser(text: str) -> object:
    """json.loads accepts Infinity/NaN by default; JSON.parse does not."""

    def reject(token: str) -> object:
        raise ValueError(f"non-JSON constant in payload: {token}")

    return json.loads(text, parse_constant=reject)


def test_raw_infinite_route_cost_breaks_browser_parsing() -> None:
    payload = {"learning": {"history": [{"planner_cost": float("inf")}]}}
    text = _serialise_like_starlette(payload)
    assert "Infinity" in text
    with pytest.raises(ValueError):
        _parse_like_browser(text)


def test_json_finite_keeps_payload_parseable_by_the_browser() -> None:
    payload = {
        "learning": {
            "history": [
                {"episode": 3, "planner_cost": float("inf"), "reward": -2.0},
                {"episode": 4, "planner_cost": float("-inf"), "reward": -2.0},
                {"episode": 5, "planner_cost": float("nan"), "reward": 1.5},
                {"episode": 6, "planner_cost": 12.5, "reward": 3.0},
            ]
        },
        "world": {"entities": [{"name": "boiler", "energy": 1.0}]},
    }
    decoded = _parse_like_browser(_serialise_like_starlette(json_finite(payload)))
    history = decoded["learning"]["history"]
    assert [row["planner_cost"] for row in history] == [None, None, None, 12.5]
    assert [row["reward"] for row in history] == [-2.0, -2.0, 1.5, 3.0]
    assert decoded["world"]["entities"][0]["name"] == "boiler"


def test_json_finite_preserves_non_float_types() -> None:
    payload = {
        "tick": 128137,
        "connected": True,
        "stage": "Scale mining",
        "missing": None,
        "series": {"iron-ore": [0, 1, 2]},
        "pair": (1.0, float("inf")),
    }
    decoded = json_finite(payload)
    assert decoded["tick"] == 128137
    assert decoded["connected"] is True
    assert decoded["stage"] == "Scale mining"
    assert decoded["missing"] is None
    assert decoded["series"]["iron-ore"] == [0, 1, 2]
    assert decoded["pair"] == [1.0, None]
