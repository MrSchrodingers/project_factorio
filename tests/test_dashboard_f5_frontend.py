from __future__ import annotations

from pathlib import Path


def test_f5_frontend_marks_completed_capability_sequence_explicitly() -> None:
    source=(
        Path(__file__).resolve().parents[1]
        /"src"/"factorio_ai_lab"/"dashboard"/"static"/"app.js"
    ).read_text(encoding="utf-8")

    assert "const phase5Complete = (" in source
    assert "A0 · F5-C COMPLETE · NO FRONTIER" in source
    assert "CORTEX F5 · F5-C COMPLETE · 9/9" in source
    assert "protocol.next_capability == null" in source


def test_f5_frontend_exposes_adaptive_autonomy_without_claiming_continuous_authority() -> None:
    source=(
        Path(__file__).resolve().parents[1]
        /"src"/"factorio_ai_lab"/"dashboard"/"static"/"app.js"
    ).read_text(encoding="utf-8")

    assert 'phase5Checkpoint === "F5-D"' in source
    assert "A0 · F5-D ADAPTIVE · A2 ONE-SHOT" in source
    assert "CORTEX F5 · F5-D ADAPTIVE · LEARNING" in source
    assert "policy escolhe, control-plane concede A2 one-shot" in source
