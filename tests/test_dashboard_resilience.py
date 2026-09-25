from __future__ import annotations

from pathlib import Path

from factorio_rcon.factorio_rcon import RCONNotConnected

from factorio_ai_lab.dashboard.state import FactorioObserver


class _StaleRconClient:
    def send_command(self, _command: str) -> str:
        raise RCONNotConnected("stale test client")


def test_snapshot_degrades_instead_of_raising_on_stale_rcon(monkeypatch) -> None:
    observer=FactorioObserver()
    observer._client=_StaleRconClient()
    monkeypatch.setattr(observer,"connected",lambda:True)

    payload=observer.snapshot()

    assert payload["connected"] is False
    assert payload["entity_count"] == 0
    assert "RCONNotConnected" in payload["error"]
    assert observer._client is None


def test_dashboard_bootstrap_is_partial_failure_tolerant() -> None:
    source=(Path(__file__).parents[1]/"src/factorio_ai_lab/dashboard/static/app.js").read_text()
    assert "Promise.allSettled(" in source
    assert "dashboard partial bootstrap" in source
    assert 'if (!response.ok)' in source
    assert "Promise.all(paths.map((path) => fetch(path)))" not in source


def test_live_socket_keeps_a_degraded_state_instead_of_reconnect_storm() -> None:
    source=(Path(__file__).parents[1]/"src/factorio_ai_lab/dashboard/app.py").read_text()
    assert 'logger.exception("dashboard live sample failed")' in source
    assert '"stream_error"' in source


def test_read_only_observers_fallback_to_world_without_agent_character() -> None:
    commands=(
        FactorioObserver._SNAPSHOT_COMMAND,
        FactorioObserver._MAP_COMMAND,
        FactorioObserver._RESOURCE_OVERVIEW_COMMAND,
    )
    for command in commands:
        assert "game.surfaces[1]" in command
        assert "game.forces.player" in command
        assert "agent character unavailable" not in command
        assert "world_fallback" in command

    knowledge=FactorioObserver._GAME_KNOWLEDGE_COMMAND
    assert "game.forces.player" in knowledge
    assert "agent character unavailable" not in knowledge


def test_snapshot_preserves_observation_origin_and_experiment_tick(monkeypatch) -> None:
    class _Client:
        def send_command(self, _command: str) -> str:
            return (
                '{"connected":true,"tick":42,"experiment_tick":7,'
                '"observer_origin":"world_fallback","entities":[]}'
            )

    observer=FactorioObserver()
    observer._client=_Client()
    monkeypatch.setattr(observer,"connected",lambda:True)

    payload=observer.snapshot()

    assert payload["connected"] is True
    assert payload["tick"] == 42
    assert payload["experiment_tick"] == 7
    assert payload["observer_origin"] == "world_fallback"
    assert payload["entity_count"] == 0


def test_dashboard_renders_f3a_as_shadow_and_keeps_g4b_as_live_evidence() -> None:
    app=(
        Path(__file__).parents[1]
        / "src/factorio_ai_lab/dashboard/static/app.js"
    ).read_text()

    assert "cortexPhase.phase3_checkpoint" in app
    assert "F3-A · Executive Shadow Kernel · SHADOW" in app
    assert "F3-A · alternativas explícitas · no live authority" in app
    assert "F2-G4B · ÚLTIMA EVIDÊNCIA LIVE VIA OPTION" in app
    assert "G4B permanece a última evidência live" in app


def test_dashboard_renders_f3b_credit_ledger_as_shadow() -> None:
    app=(
        Path(__file__).parents[1]
        / "src/factorio_ai_lab/dashboard/static/app.js"
    ).read_text()

    assert 'phase3Checkpoint === "F3-B"' in app
    assert "F3-B · Verification + Credit Ledger · SHADOW" in app
    assert "F3-B · outcomes verificados · ledger persistente" in app
    assert "verification-after-action + credit fail-closed" in app
    assert "G4B permanece a última evidência live" in app


def test_dashboard_renders_f3c_as_complete_without_superiority_claim() -> None:
    app=(
        Path(__file__).parents[1]
        / "src/factorio_ai_lab/dashboard/static/app.js"
    ).read_text()

    assert 'phase3Checkpoint === "F3-C"' in app
    assert "F3 · COMPLETE · F3-C paired shadow comparison" in app
    assert "F3 COMPLETE · escolhas explícitas · no live authority" in app
    assert "F3 Exit Gate completo sem claim de superiority" in app
    assert "G4B segue última evidência live" in app


def test_dashboard_renders_f4a_memory_substrate_without_memory_claim_inflation() -> None:
    app=(
        Path(__file__).parents[1]
        / "src/factorio_ai_lab/dashboard/static/app.js"
    ).read_text()

    assert 'phase4Checkpoint === "F4-A"' in app
    assert "F4-A · Typed Memory Substrate · SHADOW" in app
    assert "retrieval/ablation ainda abertos" in app
    assert "retrieval/ablation ainda não provados" in app
    assert "continuous authority OFF" in app
    assert "F2-G4B · ÚLTIMA EVIDÊNCIA LIVE VIA OPTION" in app


def test_dashboard_renders_f4b_retrieval_without_causal_claim() -> None:
    app=(
        Path(__file__).parents[1]
        / "src/factorio_ai_lab/dashboard/static/app.js"
    ).read_text()

    assert 'phase4Checkpoint === "F4-B"' in app
    assert "F4-B · Hybrid Retrieval + Consolidation · SHADOW" in app
    assert "retrieval/decay validados · ablation ainda aberta" in app
    assert "hybrid structural+lexical retrieval" in app
    assert "causal ablation/transfer ainda não provados" in app
    assert "continuous authority OFF" in app
