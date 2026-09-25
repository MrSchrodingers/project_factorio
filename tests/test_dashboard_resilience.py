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
