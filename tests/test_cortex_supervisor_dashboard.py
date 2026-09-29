from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_dashboard_reports_persistent_cortex_supervisor() -> None:
    state=(ROOT/"src/factorio_ai_lab/dashboard/state.py").read_text()
    app=(ROOT/"src/factorio_ai_lab/dashboard/static/app.js").read_text()
    assert 'cortex_supervisor.py' in state
    assert '"process": "cortex_supervisor"' in state
    assert '"arena": "cortex_f5"' in state
    assert "Cortex ativo · telemetria live · " in app
    assert "if (operational.phase5Active) {" in app
