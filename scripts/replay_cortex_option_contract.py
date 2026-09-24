#!/usr/bin/env python3
"""Replay historical Cortex option contracts without touching Factorio."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionProvenance,
)
from factorio_ai_lab.cortex.options import (
    OptionBudget,
    OptionKind,
    OptionRequest,
    OptionStep,
    OptionStepKind,
)
from factorio_ai_lab.paths import code_revision
from factorio_ai_lab.planning.fuel import TICKS_PER_SECOND


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _provenance(payload: dict[str, Any]) -> ActionProvenance:
    return ActionProvenance(
        requested_by=str(payload.get("requested_by") or "historical-source"),
        source_component=str(payload.get("source_component") or "historical-source"),
        code_revision=str(payload.get("code_revision") or "unknown"),
        run_id=(
            None if payload.get("run_id") is None else str(payload.get("run_id"))
        ),
        generation=payload.get("generation"),
        parent_action_id=(
            None
            if payload.get("parent_action_id") is None
            else str(payload.get("parent_action_id"))
        ),
        policy_version=(
            None
            if payload.get("policy_version") is None
            else str(payload.get("policy_version"))
        ),
    )


def build_contract_replay(
    source: dict[str, Any],
    *,
    source_path: Path,
) -> dict[str, Any]:
    plan = _object(source.get("plan"))
    parent = _object(plan.get("parent_request"))
    parent_provenance = _object(parent.get("provenance"))
    branches = plan.get("branches")
    first_branch = (
        _object(branches[0])
        if isinstance(branches, list) and branches
        else {}
    )
    child_request = _object(first_branch.get("request"))
    child_provenance = _object(child_request.get("provenance"))
    prepared = _object(source.get("prepared_v3"))
    action_result = _object(source.get("action_result"))
    functional = _object(source.get("functional_dependency"))
    delivery = _object(source.get("delivery_actuator_dependency"))
    functional_dependency = _object(functional.get("dependency"))

    horizon_s = functional_dependency.get("horizon_s")
    try:
        requested_ticks = round(float(horizon_s) * TICKS_PER_SECOND)
    except (TypeError, ValueError):
        requested_ticks = 0

    planning_instruments = _object(source.get("planning_instruments"))
    raw_input_keys = ("world", "catalog", "resources", "available")
    raw_inputs_inline = {
        key: isinstance(planning_instruments.get(key), (dict, list))
        for key in raw_input_keys
    }
    historical_inputs_replayable = all(raw_inputs_inline.values())

    parent_action_id = parent.get("action_id")
    child_action_id = child_request.get("action_id")
    checks = {
        "child_action_id_consistent": (
            bool(child_action_id)
            and child_action_id == prepared.get("action_id")
            and child_action_id == action_result.get("action_id")
        ),
        "parent_child_lineage": (
            bool(parent_action_id)
            and child_provenance.get("parent_action_id") == parent_action_id
        ),
        "code_revision_lineage": (
            bool(parent_provenance.get("code_revision"))
            and parent_provenance.get("code_revision")
            == child_provenance.get("code_revision")
        ),
        "contract_v3": prepared.get("contract_version") == "cortex_structural_ops_v3",
        "processor_dependency_ready": functional.get("ready") is True,
        "delivery_dependency_ready": delivery.get("ready") is True,
        "requested_tick_budget_available": requested_ticks > 0,
    }

    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    replay_revision = code_revision()

    option_request = None
    steps: list[dict[str, Any]] = []
    if requested_ticks > 0:
        option = OptionRequest(
            option_id=f"{source.get('run_id') or 'historical'}:processing-chain-option",
            kind=OptionKind.ESTABLISH_PROCESSING_CHAIN,
            goal="replay the historical functional processing-chain contract",
            provenance=_provenance(parent_provenance),
            budget=OptionBudget(requested_ticks=requested_ticks),
            authority=ActionAuthority.SHADOW,
        )
        option_request = option.to_dict()
        steps = [
            OptionStep(
                step_id=f"{option.option_id}:structural-plan",
                kind=OptionStepKind.PLANNER,
                component="factorio_ai_lab.cortex.structural",
                provides=("processing_branch",),
            ).to_dict(),
            OptionStep(
                step_id=f"{option.option_id}:prepare",
                kind=OptionStepKind.PREPARATION,
                component="factorio_ai_lab.cortex.structural_prepare",
                requires=("processing_branch",),
                provides=("prepared_structural_action_v1",),
            ).to_dict(),
            OptionStep(
                step_id=f"{option.option_id}:processor-energy",
                kind=OptionStepKind.DEPENDENCY,
                component="factorio_ai_lab.cortex.functional_dependency",
                requires=("prepared_structural_action_v1",),
                provides=("prepared_structural_action_v2",),
                details={"planning_horizon_ticks": requested_ticks},
            ).to_dict(),
            OptionStep(
                step_id=f"{option.option_id}:delivery-actuator-energy",
                kind=OptionStepKind.DEPENDENCY,
                component="factorio_ai_lab.cortex.delivery_actuator_dependency",
                requires=("prepared_structural_action_v2",),
                provides=("prepared_structural_action_v3",),
                details={"planning_horizon_ticks": requested_ticks},
            ).to_dict(),
        ]

    return {
        "schema_version": "cortex_option_contract_replay_v1",
        "status": "pass" if all(checks.values()) else "failed",
        "source_artifact": str(source_path),
        "source_sha256": source_sha256,
        "source_run_id": source.get("run_id"),
        "source_code_revision": source.get("code_revision"),
        "replay_code_revision": replay_revision,
        "world_mutation": False,
        "authority": ActionAuthority.SHADOW.value,
        "continuous_authority": False,
        "planner_reexecuted": False,
        "historical_inputs_replayable": historical_inputs_replayable,
        "raw_input_payloads_inline": raw_inputs_inline,
        "planning_instrument_labels": planning_instruments,
        "limitation": (
            None
            if historical_inputs_replayable
            else (
                "F2-F4C preserved instrument labels but not raw world/catalog/"
                "resources/available payloads; full planner replay is not claimed"
            )
        ),
        "option_request": option_request,
        "steps": steps,
        "prepared_contract": prepared.get("contract_version"),
        "operation_sequence": [
            row.get("op")
            for row in prepared.get("operations", [])
            if isinstance(row, dict)
        ],
        "lineage": {
            "parent_action_id": parent_action_id,
            "child_action_id": child_action_id,
            "child_parent_action_id": child_provenance.get("parent_action_id"),
            "parent_code_revision": parent_provenance.get("code_revision"),
            "child_code_revision": child_provenance.get("code_revision"),
        },
        "source_action_status": action_result.get("status"),
        "source_transaction_committed": source.get("transaction_committed"),
        "source_processor_output": _object(
            source.get("measurement_final")
        ).get("processor_output"),
        "source_processor_status": _object(
            source.get("measurement_final")
        ).get("processor_status"),
        "sustainability_evaluable": False,
        "observed_ticks": None,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()

    source = json.loads(args.source.read_text(encoding="utf-8"))
    replay = build_contract_replay(source, source_path=args.source)
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(
        json.dumps(replay, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(replay, indent=2, sort_keys=True))
    return 0 if replay["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
