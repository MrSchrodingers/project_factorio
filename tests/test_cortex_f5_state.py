from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _module():
    path=ROOT/"scripts"/"init_cortex_f5_state.py"
    spec=importlib.util.spec_from_file_location("init_cortex_f5_state",path)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema(path: Path) -> None:
    path.write_text(json.dumps({
        "ledger_schema_version":"cortex_f5_intervention_ledger_v1",
        "protocol_id":"cortex-f5-autonomous-factory-bootstrap-v1",
    })+"\n")


def test_initializer_creates_empty_ledger_idempotently(tmp_path: Path) -> None:
    module=_module()
    schema=tmp_path/"schema.json"
    _schema(schema)

    path,created=module.ensure_intervention_ledger(
        state_root=tmp_path,
        schema_path=schema,
    )
    assert created is True
    payload=json.loads(path.read_text())
    assert payload=={
        "schema_version":"cortex_f5_intervention_ledger_v1",
        "protocol_id":"cortex-f5-autonomous-factory-bootstrap-v1",
        "interventions":[],
    }

    same_path,created_again=module.ensure_intervention_ledger(
        state_root=tmp_path,
        schema_path=schema,
    )
    assert same_path==path
    assert created_again is False


def test_initializer_refuses_protocol_mismatch(tmp_path: Path) -> None:
    module=_module()
    schema=tmp_path/"schema.json"
    _schema(schema)
    runs=tmp_path/"runs"
    runs.mkdir()
    (runs/"cortex_f5_intervention_ledger.json").write_text(json.dumps({
        "schema_version":"cortex_f5_intervention_ledger_v1",
        "protocol_id":"wrong",
        "interventions":[],
    })+"\n")

    try:
        module.ensure_intervention_ledger(
            state_root=tmp_path,
            schema_path=schema,
        )
    except ValueError as exc:
        assert "protocol mismatch" in str(exc)
    else:
        raise AssertionError("protocol mismatch must fail closed")
