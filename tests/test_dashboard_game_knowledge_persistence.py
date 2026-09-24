from __future__ import annotations

import json

from factorio_ai_lab.dashboard import state as state_module
from factorio_ai_lab.dashboard.state import FactorioObserver


class FakeRconClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def send_command(self, _command: str) -> str:
        return json.dumps(self.payload)

    def close(self) -> None:
        pass


def valid_payload() -> dict:
    return {
        "connected": True,
        "recipes": [{"name": "iron-plate"}],
        "technologies": [{"name": "automation"}],
        "machines": [{"name": "stone-furnace"}],
        "belts": [{"name": "transport-belt"}],
        "counts": {
            "recipes": 1,
            "technologies": 1,
            "machines": 1,
            "belts": 1,
        },
    }


def test_game_knowledge_persists_only_valid_catalog(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)
    observer = FactorioObserver()
    observer._client = FakeRconClient(valid_payload())

    payload = observer.game_knowledge(max_age_s=0)

    assert payload["connected"] is True
    persisted = json.loads((tmp_path / "game_knowledge_graph.json").read_text())
    assert persisted == valid_payload()


def test_transient_character_error_does_not_overwrite_valid_catalog(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(state_module, "RUNS_DIR", tmp_path)
    path = tmp_path / "game_knowledge_graph.json"
    path.write_text(json.dumps(valid_payload()) + "\n")

    observer = FactorioObserver()
    observer._client = FakeRconClient(
        {"connected": False, "error": "agent character unavailable"}
    )

    payload = observer.game_knowledge(max_age_s=0)

    assert payload["connected"] is False
    assert "agent character unavailable" in payload["error"]
    assert json.loads(path.read_text()) == valid_payload()
