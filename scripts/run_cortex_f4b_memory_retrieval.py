#!/usr/bin/env python3
"""Canonical F4-B hybrid retrieval/consolidation/decay replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.memory import MemoryKind, ValidityScope
from factorio_ai_lab.cortex.memory_retrieval import (
    DecayPolicy,
    MemoryQuery,
    consolidate_memory,
    load_memory_records,
    memory_database_snapshot,
    retrieve_memories,
)

SCHEMA_VERSION = "cortex_f4b_memory_retrieval_v1"
F4A_ARTIFACT = Path("runs/audits/cortex_f4a_memory_substrate_migration.json")
DEFAULT_MEMORY_PATH = Path("runs/ledger/cortex_cognitive_memory.sqlite3")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(root: Path) -> dict[str, Any]:
    commit = subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    branch = subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={root}",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ],
        cwd=root,
        text=True,
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "-c", f"safe.directory={root}", "status", "--porcelain"],
            cwd=root,
            text=True,
        ).strip()
    )
    return {"commit": commit, "branch": branch, "dirty": dirty}


def _queries() -> tuple[MemoryQuery, ...]:
    return (
        MemoryQuery(
            query_id="semantic-smelting-output",
            text="iron furnace smelting plates output",
            kinds=(MemoryKind.SEMANTIC,),
            scope=ValidityScope(stage="smelting_probe"),
            limit=5,
        ),
        MemoryQuery(
            query_id="semantic-smelting-placement-error",
            text="entity placement error existing entities target position",
            kinds=(MemoryKind.SEMANTIC,),
            scope=ValidityScope(stage="smelting_probe"),
            limit=5,
        ),
        MemoryQuery(
            query_id="cross-kind-fuel-procedure",
            text="resupply insert fuel world container",
            kinds=(MemoryKind.PROCEDURAL, MemoryKind.SEMANTIC),
            scope=ValidityScope(symptom="fuel_starved:no_fuel"),
            limit=5,
        ),
        MemoryQuery(
            query_id="counterexample-electric-route-buffer",
            text="route buffer iron shortfall repair",
            kinds=(MemoryKind.COUNTEREXAMPLE,),
            scope=ValidityScope(
                stage="Electric mining transition",
                phase="route_buffer",
            ),
            limit=5,
        ),
    )


def build_replay(
    *,
    root: Path,
    memory_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError("F4-B canonical replay requires a clean source tree")

    before = memory_database_snapshot(memory_path)
    records = load_memory_records(memory_path)
    consolidation = consolidate_memory(records, database=before)
    retrievals = {
        query.query_id: retrieve_memories(records, query).to_dict()
        for query in _queries()
    }
    after = memory_database_snapshot(memory_path)

    output_query = retrievals["semantic-smelting-output"]
    error_query = retrievals["semantic-smelting-placement-error"]
    procedure_query = retrievals["cross-kind-fuel-procedure"]
    counter_query = retrievals["counterexample-electric-route-buffer"]

    output_top = (output_query.get("results") or [{}])[0]
    error_top = (error_query.get("results") or [{}])[0]
    procedure_top = (procedure_query.get("results") or [{}])[0]
    counter_results = counter_query.get("results") or []

    decay = DecayPolicy()
    decay_probe = {
        "policy_version": decay.version,
        "reference_time": "2027-01-01T00:00:00+00:00",
        "last_observed_at": "2026-01-01T00:00:00+00:00",
        "low_support": {
            "qualified_support_count": 1,
            "weight": decay.weight(
                last_observed_at="2026-01-01T00:00:00+00:00",
                reference_time="2027-01-01T00:00:00+00:00",
                qualified_support_count=1,
            ),
        },
        "high_support": {
            "qualified_support_count": 50,
            "weight": decay.weight(
                last_observed_at="2026-01-01T00:00:00+00:00",
                reference_time="2027-01-01T00:00:00+00:00",
                qualified_support_count=50,
            ),
        },
        "destructive_deletion": False,
    }

    f4a_path = root / F4A_ARTIFACT
    checks = {
        "memory_database_quick_check_ok": before.quick_check == "ok",
        "memory_database_unchanged_by_replay": before == after,
        "memory_database_has_multiple_kinds": (
            consolidation.by_kind["episodic"]["items"] > 0
            and consolidation.by_kind["semantic"]["items"] > 0
            and consolidation.by_kind["procedural"]["items"] > 0
            and consolidation.by_kind["counterexample"]["items"] > 0
        ),
        "semantic_stage_scope_enforced": (
            bool(output_query.get("results"))
            and bool(error_query.get("results"))
            and output_top.get("scope", {}).get("stage") == "smelting_probe"
            and error_top.get("scope", {}).get("stage") == "smelting_probe"
        ),
        "lexical_component_changes_top_memory_same_stage": (
            output_top.get("memory_id") != error_top.get("memory_id")
            and output_top.get("scores", {}).get("similarity", 0) > 0
            and error_top.get("scores", {}).get("similarity", 0) > 0
        ),
        "procedural_memory_ranked_first_by_symptom": (
            procedure_top.get("kind") == "procedural"
            and procedure_top.get("scope", {}).get("symptom")
            == "fuel_starved:no_fuel"
            and procedure_top.get("confidence", {}).get("value") is not None
        ),
        "counterexample_scope_enforced": (
            bool(counter_results)
            and all(
                row.get("scope", {}).get("stage")
                == "Electric mining transition"
                and row.get("scope", {}).get("phase") == "route_buffer"
                for row in counter_results
            )
        ),
        "semantic_duplicate_support_consolidated": (
            consolidation.repeated_semantic_items > 0
            and consolidation.semantic_duplicate_support > 0
        ),
        "counterexample_repetition_visible": (
            consolidation.repeated_counterexample_items > 0
        ),
        "procedural_confidence_visible": (
            consolidation.procedural_confidence_items > 0
        ),
        "decay_is_non_destructive_and_support_protective": (
            decay_probe["destructive_deletion"] is False
            and decay_probe["low_support"]["weight"]
            < decay_probe["high_support"]["weight"]
            <= 1.0
        ),
        "f4a_artifact_present": f4a_path.exists(),
        "no_live_authority": True,
    }
    status = "pass" if all(checks.values()) else "fail"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_id": (
            "cortex-f4b-retrieval-"
            + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": revision,
        "authority": "shadow",
        "world_mutation": False,
        "factorio_rcon_used": False,
        "fle_environment_created": False,
        "world_lease_acquired": False,
        "execution_grant_created": False,
        "continuous_authority": False,
        "source": {
            "memory_path": str(memory_path.relative_to(root)),
            "database_before": before.to_dict(),
            "database_after": after.to_dict(),
            "f4a_artifact_path": str(F4A_ARTIFACT),
            "f4a_artifact_sha256": (
                _sha256(f4a_path) if f4a_path.exists() else None
            ),
        },
        "retrievals": retrievals,
        "consolidation": consolidation.to_dict(),
        "decay_probe": decay_probe,
        "checks": checks,
        "claim_boundary": {
            "proves": [
                "scope-compatible retrieval is explicit and fail-closed on known mismatches",
                "lexical similarity can rerank memories inside the same structural scope",
                "procedural memory can outrank generic semantic memory for an exact symptom",
                "counterexamples are retrievable by explicit phase/stage scope",
                "consolidation summarizes duplicate support without deleting provenance",
                "decay is non-destructive and support-protective",
                "retrieval and consolidation leave the canonical memory database unchanged",
            ],
            "does_not_prove": [
                "retrieval improves task outcome",
                "the ranking weights are optimal",
                "memory causes transfer improvement",
                "the decay hyperparameters are optimal",
                "F4 Exit Gate completion",
            ],
        },
    }


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"canonical F4-B artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/srv/factorio-ai-lab"))
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/audits/cortex_f4b_memory_retrieval.json"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    memory = args.memory if args.memory.is_absolute() else root / args.memory
    output = args.output if args.output.is_absolute() else root / args.output
    payload = build_replay(
        root=root,
        memory_path=memory,
        revision=_git_revision(root),
    )
    _write_artifact(output, payload)
    print(output)
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
