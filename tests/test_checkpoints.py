import json
from pathlib import Path

from factorio_ai_lab.learning.checkpoints import save_game_state


class FakeGameState:
    def to_raw(self) -> str:
        return json.dumps(
            {
                "entities": "compressed-entities",
                "inventories": [{"iron-plate": 3}],
                "timestamp": 1.0,
                "namespaces": [""],
                "agent_messages": [[]],
            }
        )


def test_save_game_state_is_atomic_and_auditable(tmp_path: Path):
    path = tmp_path / "state.json"
    saved = save_game_state(
        path,
        FakeGameState(),
        run_id="run-1",
        arena="open_play",
        qualified=True,
    )

    assert path.exists()
    assert saved.run_id == "run-1"
    assert saved.arena == "open_play"
    assert saved.qualified is True
    meta = json.loads(
        path.with_suffix(".json.meta.json").read_text()
    )
    assert meta["sha256"] == saved.sha256
    assert meta["bytes"] == saved.bytes


def test_save_game_state_rejects_incomplete_envelope(tmp_path: Path):
    class Invalid:
        def to_raw(self) -> str:
            return json.dumps({"entities": "x"})

    try:
        save_game_state(tmp_path / "bad.json", Invalid())
    except ValueError as exc:
        assert "entities/inventories" in str(exc)
    else:
        raise AssertionError("incomplete checkpoint should fail")
