#!/usr/bin/env python3
"""F2-G4A deterministic dry-run for persistent Option authority.

This runner deliberately does not create a Factorio environment, connect to
RCON, import a legacy stage runner, or call Option EXECUTE. It validates the
persistent one-shot grant contract against a deterministic composed Option and
persists auditable positive/negative-control evidence.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.actions import (
    ActionAuthority,
    ActionProvenance,
)
from factorio_ai_lab.cortex.executor import request_from_repair_action
from factorio_ai_lab.cortex.grant_ledger import (
    LEDGER_READY,
    OptionExecutionGrant,
    OptionExecutionScope,
    PersistentOptionGrantLedger,
)
from factorio_ai_lab.cortex.option_execute import (
    REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH,
    OptionExecutionBoundary,
)
from factorio_ai_lab.cortex.options import (
    OptionBudget,
    OptionKind,
    OptionRequest,
    compose_processing_chain_option,
)
from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.learning.repair_loop import (
    INTENT_PLACE_PROCESSING,
    TOOL_PLACEMENT,
    Prediction,
    RepairAction,
)
from factorio_ai_lab.paths import code_revision
from factorio_ai_lab.planning.runtime_catalog import (
    PROBE_ABSENT,
    PROBE_MEASURED,
    RuntimeFactorioCatalog,
)

SCHEMA_VERSION = "cortex_f2g4a_option_authority_dry_run_v1"


def _entity(
    name: str,
    x: float,
    y: float,
    *,
    unit: int,
    contents: list[dict[str, Any]] | None = None,
    direction: int = 0,
) -> dict[str, Any]:
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "unit_number": unit,
        "direction": direction,
        "status": "working",
        "contents": [] if contents is None else deepcopy(contents),
    }


def _catalog() -> RuntimeFactorioCatalog:
    return RuntimeFactorioCatalog(
        {
            "recipes": [
                {
                    "name": "iron-plate",
                    "energy": 3.2,
                    "categories": ["smelting"],
                    "ingredients": [{"name": "iron-ore", "amount": 1}],
                    "products": [{"name": "iron-plate", "amount": 1}],
                    "enabled": True,
                },
                {
                    "name": "stone-furnace",
                    "energy": 0.5,
                    "categories": ["crafting"],
                    "ingredients": [{"name": "stone", "amount": 5}],
                    "products": [{"name": "stone-furnace", "amount": 1}],
                    "enabled": True,
                },
            ],
            "technologies": [],
            "machines": [
                {
                    "name": "character",
                    "type": "character",
                    "crafting_categories": ["crafting"],
                    "crafting_speed": 1.0,
                    "crafting_speed_status": PROBE_MEASURED,
                    "energy_source_status": PROBE_ABSENT,
                    "energy_usage_status": PROBE_ABSENT,
                    "fuel_categories_status": PROBE_ABSENT,
                },
                {
                    "name": "stone-furnace",
                    "type": "furnace",
                    "crafting_categories": ["smelting"],
                    "crafting_speed": 1.0,
                    "crafting_speed_status": PROBE_MEASURED,
                    "energy_source_type": "burner",
                    "energy_source_status": PROBE_MEASURED,
                    "energy_usage_per_tick_j": 1500,
                    "energy_usage_status": PROBE_MEASURED,
                    "fuel_categories": ["chemical"],
                    "fuel_categories_status": PROBE_MEASURED,
                },
                {
                    "name": "inserter",
                    "type": "inserter",
                    "energy_source_type": "electric",
                    "energy_source_status": PROBE_MEASURED,
                    "energy_usage_per_tick_j": 245,
                    "energy_usage_status": PROBE_MEASURED,
                    "fuel_categories": [],
                    "fuel_categories_status": PROBE_ABSENT,
                },
                {
                    "name": "burner-inserter",
                    "type": "inserter",
                    "energy_source_type": "burner",
                    "energy_source_status": PROBE_MEASURED,
                    "energy_usage_per_tick_j": 2400,
                    "energy_usage_status": PROBE_MEASURED,
                    "fuel_categories": ["chemical"],
                    "fuel_categories_status": PROBE_MEASURED,
                },
            ],
            "fuels": [
                {
                    "name": "coal",
                    "fuel_value_j": 4_000_000,
                    "fuel_value_status": PROBE_MEASURED,
                    "fuel_categories": ["chemical"],
                    "fuel_categories_status": PROBE_MEASURED,
                }
            ],
            "belts": [],
        }
    )


def _build_plan(*, commit: str, run_id: str):
    world = [
        _entity("character", -5, -5, unit=99),
        _entity("burner-mining-drill", 0, 0, unit=1, direction=8),
        _entity(
            "wooden-chest",
            0,
            2,
            unit=2,
            contents=[{"name": "iron-ore", "count": 20}],
        ),
    ]
    provenance = ActionProvenance(
        requested_by="f2-g4a-dry-run",
        source_component="scripts.run_cortex_option_authority_dry_run",
        code_revision=commit,
        run_id=run_id,
    )
    repair = RepairAction(
        tool=TOOL_PLACEMENT,
        intent=INTENT_PLACE_PROCESSING,
        prediction=Prediction(
            "physical_factory_graph.producers_reaching_processor",
            "increase",
        ),
        provides=("material",),
        targets=("u1",),
        arguments={"producers": ["u1"]},
    )
    action = request_from_repair_action(
        repair,
        action_id=f"{run_id}-action",
        provenance=provenance,
    )
    option = OptionRequest(
        option_id=f"{run_id}-processing-chain",
        kind=OptionKind.ESTABLISH_PROCESSING_CHAIN,
        goal="validate persistent one-shot authority without world mutation",
        provenance=provenance,
        budget=OptionBudget(requested_ticks=600),
        authority=ActionAuthority.SHADOW,
    )
    composed = compose_processing_chain_option(
        option,
        action_request=action,
        graph=build_factory_graph(world),
        world_entities=world,
        catalog=_catalog(),
        inventory={
            "stone": 5,
            "inserter": 50,
            "burner-inserter": 50,
            "coal": 480,
            "transport-belt": 20,
        },
        electric_power_available=False,
    )
    if not composed.ready or composed.plan is None:
        refusal = None if composed.refusal is None else composed.refusal.to_dict()
        raise RuntimeError(f"deterministic Option fixture refused: {refusal}")
    return composed.plan


def run_dry_run(
    *,
    ledger_path: Path,
    artifact_path: Path,
    revision: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    commit = revision.get("commit")
    if not isinstance(commit, str) or not commit:
        raise RuntimeError("dry-run requires an exact code revision")
    if revision.get("dirty") is not False:
        raise RuntimeError("dry-run requires a clean committed source tree")

    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    run_id = instant.astimezone(UTC).strftime(
        "cortex-f2g4a-dry-%Y%m%dT%H%M%SZ"
    )
    plan = _build_plan(commit=commit, run_id=run_id)
    scope = OptionExecutionScope.for_plan(
        plan,
        experiment_id=run_id,
        world_lease_id="dry-run:no-live-factorio-world",
    )
    grant = OptionExecutionGrant.for_plan(
        plan,
        scope=scope,
        issued_by="f2-g4a-dry-run",
        reason="validate persistent one-shot authority before live G4B",
        ttl_seconds=900,
        now=instant,
    )

    ledger = PersistentOptionGrantLedger(ledger_path)
    ledger.issue(grant)
    boundary = OptionExecutionBoundary(ledger=ledger)
    validation = boundary.validate(
        plan,
        grant=grant,
        scope=scope,
        now=instant,
    )

    wrong_scope = replace(
        scope,
        world_lease_id="dry-run:negative-control-wrong-world",
    )
    negative = boundary.validate(
        plan,
        grant=grant,
        scope=wrong_scope,
        now=instant,
    )
    entry = ledger.get(grant.grant_id)

    negative_code = (
        None if negative.refusal is None else negative.refusal.code
    )
    passed = bool(
        validation.valid
        and validation.ledger_status == LEDGER_READY
        and not negative.valid
        and negative_code == REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH
        and entry is not None
        and entry.consumed_at is None
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": "pass" if passed else "fail",
        "mode": "dry_run",
        "environment": "deterministic_synthetic_option_fixture",
        "code_revision": revision,
        "confirmatory_seed": False,
        "factorio_environment_created": False,
        "factorio_rcon_used": False,
        "factorio_world_mutation": False,
        "continuous_authority": False,
        "live_option_execute_authorized": False,
        "ledger_path": str(ledger_path),
        "plan_digest": validation.plan_digest,
        "grant": grant.to_dict(),
        "validation": validation.to_dict(),
        "negative_control": {
            "kind": "scope_mismatch",
            "validation": negative.to_dict(),
        },
        "ledger_entry": None if entry is None else entry.to_dict(),
        "invariants": {
            "persistent_grant_resolved": validation.valid,
            "exact_scope_required": (
                not negative.valid
                and negative_code
                == REFUSAL_OPTION_EXECUTION_GRANT_MISMATCH
            ),
            "dry_run_did_not_consume_grant": (
                entry is not None and entry.consumed_at is None
            ),
            "world_mutation": False,
            "continuous_authority": False,
        },
        "recorded_at": instant.astimezone(UTC).isoformat(),
    }

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(artifact_path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ledger",
        type=Path,
        default=Path(
            "/srv/factorio-ai-lab/runs/authority/"
            "cortex_option_grants.sqlite3"
        ),
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=Path(
            "/srv/factorio-ai-lab/runs/audits/"
            "cortex_f2g4a_option_authority_dry_run.json"
        ),
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    revision = code_revision(root=root)
    payload = run_dry_run(
        ledger_path=args.ledger.resolve(),
        artifact_path=args.artifact.resolve(),
        revision=revision,
    )
    print(args.artifact.resolve())
    print(json.dumps({"status": payload["status"], "run_id": payload["run_id"]}))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
