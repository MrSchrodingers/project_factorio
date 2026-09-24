from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "scripts" / "replay_cortex_option_contract.py"
    spec = importlib.util.spec_from_file_location("cortex_option_contract_replay", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_historical_contract_replay_is_explicitly_partial(tmp_path) -> None:
    module = _module()
    source_path = tmp_path / "source.json"
    source = {
        "run_id": "historical-run",
        "code_revision": {"commit": "source-sha", "dirty": False},
        "planning_instruments": {
            "world": "FactorioObserver.snapshot",
            "catalog": "FactorioObserver.game_knowledge",
            "resources": "FactorioObserver.resource_overview",
            "available": "FLE character inventory",
        },
        "plan": {
            "parent_request": {
                "action_id": "parent-a1",
                "provenance": {
                    "requested_by": "historical",
                    "source_component": "canary",
                    "code_revision": "source-sha",
                    "run_id": "historical-run",
                },
            },
            "branches": [
                {
                    "request": {
                        "action_id": "a1",
                        "provenance": {
                            "requested_by": "historical",
                            "source_component": "factorio_ai_lab.cortex.structural",
                            "code_revision": "source-sha",
                            "run_id": "historical-run",
                            "parent_action_id": "parent-a1",
                        },
                    },
                },
            ],
        },
        "prepared_v3": {
            "action_id": "a1",
            "contract_version": "cortex_structural_ops_v3",
            "operations": [
                {"op": "fuel_processor"},
                {"op": "connect_delivery"},
                {"op": "fuel_delivery_actuator"},
                {"op": "verify_postconditions"},
            ],
        },
        "functional_dependency": {
            "ready": True,
            "dependency": {"horizon_s": 10.0},
        },
        "delivery_actuator_dependency": {"ready": True},
        "action_result": {"action_id": "a1", "status": "accepted"},
        "transaction_committed": True,
        "measurement_final": {
            "processor_output": 13.0,
            "processor_status": "no_fuel",
        },
    }
    source_path.write_text(json.dumps(source) + "\n", encoding="utf-8")

    replay = module.build_contract_replay(source, source_path=source_path)

    assert replay["status"] == "pass"
    assert replay["world_mutation"] is False
    assert replay["authority"] == "shadow"
    assert replay["planner_reexecuted"] is False
    assert replay["historical_inputs_replayable"] is False
    assert replay["sustainability_evaluable"] is False
    assert replay["option_request"]["budget"]["requested_ticks"] == 600
    assert replay["option_request"]["budget"]["effective_ticks"] is None
    assert replay["source_processor_output"] == 13.0
    assert replay["source_processor_status"] == "no_fuel"
    assert replay["checks"]["child_action_id_consistent"] is True
    assert replay["checks"]["parent_child_lineage"] is True
    assert replay["checks"]["code_revision_lineage"] is True
    assert replay["lineage"]["parent_action_id"] == "parent-a1"
    assert replay["lineage"]["child_action_id"] == "a1"
