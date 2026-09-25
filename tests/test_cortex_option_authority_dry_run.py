from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path


def _module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "run_cortex_option_authority_dry_run.py"
    )
    spec = importlib.util.spec_from_file_location(
        "run_cortex_option_authority_dry_run",
        path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dry_run_artifact_is_non_mutating_and_grant_remains_fresh(
    tmp_path: Path,
) -> None:
    module = _module()
    artifact = tmp_path / "artifact.json"
    ledger = tmp_path / "ledger.sqlite3"

    payload = module.run_dry_run(
        ledger_path=ledger,
        artifact_path=artifact,
        revision={
            "commit": "f2g4a-test-sha",
            "branch": "research/cortex-v1",
            "dirty": False,
            "source": "test",
        },
        now=datetime(2026, 9, 24, 12, 0, tzinfo=UTC),
    )

    assert payload["status"] == "pass"
    assert payload["factorio_environment_created"] is False
    assert payload["factorio_rcon_used"] is False
    assert payload["factorio_world_mutation"] is False
    assert payload["continuous_authority"] is False
    assert payload["live_option_execute_authorized"] is False
    assert payload["validation"]["valid"] is True
    assert payload["validation"]["ledger_status"] == "ready"
    assert payload["negative_control"]["validation"]["valid"] is False
    assert payload["ledger_entry"]["consumed_at"] is None
    assert payload["ledger_entry"]["consume_result"] is None
    assert artifact.exists()
    assert ledger.exists()
