from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def _module():
    path=ROOT/"scripts"/"validate_cortex_f5b_authority_bridge.py"
    spec=importlib.util.spec_from_file_location(
        "validate_cortex_f5b_authority_bridge",
        path,
    )
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _phase_state():
    return {
        "phase":"F5",
        "phase5_checkpoint":"F5-A",
        "phase5_next_checkpoint":"F5-B",
        "phase5_protocol":{
            "validated":True,
            "authority_level":"A0",
            "world_mutation_authorized":False,
            "continuous_authority":False,
        },
    }


def _interventions():
    return {
        "schema_version":"cortex_f5_intervention_ledger_v1",
        "protocol_id":"cortex-f5-autonomous-factory-bootstrap-v1",
        "interventions":[],
    }


def test_audit_is_read_only_and_closes_only_control_plane(monkeypatch) -> None:
    module=_module()
    monkeypatch.setattr(
        module,
        "code_revision",
        lambda:{
            "commit":"f5b-test-sha",
            "branch":"research/cortex-v1",
            "dirty":False,
        },
    )
    monkeypatch.setattr(module,"phase_state",_phase_state)
    monkeypatch.setattr(
        module,
        "evolution_service_state",
        lambda:{"active":"inactive","enabled":"disabled"},
    )
    monkeypatch.setattr(
        module,
        "world_lease_state",
        lambda:{"status":"released"},
    )
    monkeypatch.setattr(module,"intervention_state",_interventions)
    monkeypatch.setattr(
        module,
        "run_targeted_tests",
        lambda:{
            "command":["pytest"],
            "returncode":0,
            "passed":True,
            "output_tail":"36 passed",
        },
    )

    audit=module.build_audit()

    assert audit["status"]=="pass"
    assert audit["checkpoint"]=="F5-B"
    assert audit["authority"]=={
        "ambient_level":"A0",
        "bounded_grant_level":"A2",
        "max_executions":1,
        "continuous_authority":False,
        "policy_may_self_grant_authority":False,
    }
    assert audit["world_mutation"] is False
    assert audit["world_lease_acquired"] is False
    assert audit["grant_issued"] is False
    assert audit["option_executed_live"] is False
    assert audit["next_checkpoint"]=="F5-C"
    assert all(audit["checks"].values())


def test_audit_fails_closed_if_evolution_is_active(monkeypatch) -> None:
    module=_module()
    monkeypatch.setattr(
        module,
        "code_revision",
        lambda:{"commit":"f5b-test-sha","dirty":False},
    )
    monkeypatch.setattr(module,"phase_state",_phase_state)
    monkeypatch.setattr(
        module,
        "evolution_service_state",
        lambda:{"active":"active","enabled":"disabled"},
    )
    monkeypatch.setattr(module,"world_lease_state",lambda:{"status":"absent"})
    monkeypatch.setattr(module,"intervention_state",_interventions)
    monkeypatch.setattr(
        module,
        "run_targeted_tests",
        lambda:{
            "command":["pytest"],
            "returncode":0,
            "passed":True,
            "output_tail":"pass",
        },
    )

    audit=module.build_audit()

    assert audit["status"]=="failed"
    assert audit["checks"]["evolution_inactive_disabled"] is False
    assert audit["world_mutation"] is False
