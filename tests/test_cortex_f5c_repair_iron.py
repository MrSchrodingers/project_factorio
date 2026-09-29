from __future__ import annotations

from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_repair_runner_is_live_no_reset_and_no_promotion_credit() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_repair_iron.py").read_text()

    assert "attach_live_factorio_environment()" in source
    assert "executor.reset(" not in source
    assert '"world_reset":False' in source
    assert '"promotion_credit":False' in source
    assert '"capability_promoted":None' in source
    assert "F5BoundedAuthorityBridge" in source
    assert "actual!=target" in source
    assert "live FLE attachment changed WORLD before recovery A2" in source


def test_supervisor_uses_dedicated_no_reset_repair() -> None:
    source=(ROOT/"scripts"/"run_cortex_supervisor.py").read_text()

    assert 'repair_script="run_cortex_f5c_repair_iron.py"' in source
    assert 'payload["repair_mode"]="technical_recovery_no_reset"' in source
    repair_block=source[source.index(
        'repair_script="run_cortex_f5c_repair_iron.py"'
    ):]
    assert 'runner_args("iron_extraction",state)' not in repair_block


def test_repair_live_action_uses_checkpoint_only_for_rollback() -> None:
    source=(ROOT/"scripts"/"run_cortex_f5c_repair_iron.py").read_text()

    assert "use_checkpoint_for_action=False" in source
    assert "use_checkpoint_for_action=True" not in source
