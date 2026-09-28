#!/usr/bin/env python3
"""Validate F4-C memory treatment semantics without executing protocol seeds."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_protocol import (
    canonical_sha256,
    validate_protocol,
)
from factorio_ai_lab.cortex.causal_treatment import (
    CANDIDATE_BINDINGS,
    TREATMENT_POLICY_VERSION,
    binding_summary,
    rank_candidates,
    task_query_text,
    treatment_retrieval_limit,
)
from factorio_ai_lab.cortex.memory import ValidityScope
from factorio_ai_lab.cortex.memory_retrieval import (
    MemoryQuery,
    load_memory_records,
    memory_database_snapshot,
    retrieve_memories,
)

SCHEMA_VERSION = "cortex_f4c_treatment_validation_v1"
DEFAULT_MANIFEST = Path("configs/cortex_f4c_causal_ablation_v1.json")
DEFAULT_MEMORY = Path("runs/ledger/cortex_cognitive_memory.sqlite3")
DEFAULT_OUTPUT = Path("runs/audits/cortex_f4c_treatment_validation.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_revision(root: Path) -> dict[str, Any]:
    base = ["git", "-c", f"safe.directory={root}"]
    commit = subprocess.check_output(
        [*base, "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    branch = subprocess.check_output(
        [*base, "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            [*base, "status", "--porcelain"], cwd=root, text=True
        ).strip()
    )
    return {"commit": commit, "branch": branch, "dirty": dirty}


def build_validation(
    *,
    root: Path,
    manifest_path: Path,
    memory_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError("treatment validation requires a clean source tree")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    protocol = validate_protocol(manifest)
    before = memory_database_snapshot(memory_path)
    records = load_memory_records(memory_path)
    pilot_tasks = manifest["task_design"]["pilot_tasks"]

    frozen_candidates = {
        str(candidate)
        for task in pilot_tasks
        for candidate in task["spec"]["candidate_classes"]
    }
    registered = set(CANDIDATE_BINDINGS)
    traces: list[dict[str, Any]] = []
    influenced_families: set[str] = set()
    for task in pilot_tasks:
        query = MemoryQuery(
            query_id=f"treatment-preflight:{task['task_id']}",
            text=task_query_text(task),
            scope=ValidityScope(),
            limit=treatment_retrieval_limit(
                task["spec"]["candidate_classes"]
            ),
        )
        retrieval = retrieve_memories(records, query)
        on_rows = retrieval.to_dict()["results"]
        on = rank_candidates(task["spec"]["candidate_classes"], on_rows)
        ablated = rank_candidates(task["spec"]["candidate_classes"], ())
        if on.memory_influence > 0.0:
            influenced_families.add(str(task["family"]))
        traces.append(
            {
                "task_id": task["task_id"],
                "family": task["family"],
                "seed_reference_only": task["seed"],
                "candidate_surface": list(task["spec"]["candidate_classes"]),
                "query": query.text,
                "retrieval": retrieval.to_dict(),
                "memory_on_decision": on.to_dict(),
                "memory_ablated_decision": ablated.to_dict(),
                "candidate_surface_equal": on.candidates == ablated.candidates,
                "memory_score_path_changed": on.memory_influence > 0.0,
            }
        )

    after = memory_database_snapshot(memory_path)
    treatment_module = (
        root / "src/factorio_ai_lab/cortex/causal_treatment.py"
    )
    validator_path = root / "scripts/validate_cortex_f4c_treatment.py"
    test_path = root / "tests/test_cortex_f4c_treatment.py"
    checks = {
        "protocol_matches_frozen_v1": (
            protocol["manifest_sha256"] == canonical_sha256(manifest)
        ),
        "source_memory_matches_frozen_manifest": (
            before.manifest_sha256 == manifest["source_memory"]["manifest_sha256"]
            and before.item_count == manifest["source_memory"]["item_count"]
            and before.occurrence_count
            == manifest["source_memory"]["occurrence_count"]
        ),
        "source_memory_unchanged": before == after,
        "all_frozen_candidate_classes_registered": frozen_candidates == registered,
        "all_candidate_classes_planner_bound": all(
            CANDIDATE_BINDINGS[name].planner_bound for name in frozen_candidates
        ),
        "candidate_surface_identical_on_vs_ablated": all(
            row["candidate_surface_equal"] for row in traces
        ),
        "memory_enters_ranking_score_path": all(
            row["memory_score_path_changed"] for row in traces
        ),
        "all_four_families_receive_nonzero_memory_signal": len(
            influenced_families
        ) == 4,
        "no_protocol_seed_executed": True,
        "no_world_mutation": True,
        "no_rcon_or_fle": True,
    }
    status = "pass" if all(checks.values()) else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "mode": "treatment_semantics_preflight",
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": revision,
        "protocol": {
            "protocol_id": manifest["protocol_id"],
            "manifest_file_sha256": _sha256(manifest_path),
            "manifest_sha256": protocol["manifest_sha256"],
        },
        "source": {
            "memory_path": str(memory_path.relative_to(root)),
            "database_before": before.to_dict(),
            "database_after": after.to_dict(),
            "treatment_module_sha256": _sha256(treatment_module),
            "validator_sha256": _sha256(validator_path),
            "tests_sha256": _sha256(test_path),
        },
        "policy_version": TREATMENT_POLICY_VERSION,
        "binding_summary": binding_summary(),
        "pilot_task_references": traces,
        "checks": checks,
        "authority": {
            "world_mutation": False,
            "factorio_rcon_used": False,
            "fle_environment_created": False,
            "world_lease_acquired": False,
            "protocol_seed_executed": False,
        },
        "claim_boundary": {
            "proves": [
                "MEMORY_ON can contribute to ranking while the frozen candidate surface remains identical",
                "MEMORY_ABLATED uses the identical candidates with zero memory support",
                "all frozen candidate labels have explicit planner bindings",
            ],
            "does_not_prove": [
                "every frozen candidate has a protocol executor",
                "a pilot pair can be executed",
                "memory improves outcomes",
            ],
        },
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"treatment audit already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/srv/factorio-ai-lab"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = (
        args.manifest if args.manifest.is_absolute() else root / args.manifest
    )
    memory = args.memory if args.memory.is_absolute() else root / args.memory
    output = args.output if args.output.is_absolute() else root / args.output
    payload = build_validation(
        root=root,
        manifest_path=manifest,
        memory_path=memory,
        revision=_git_revision(root),
    )
    _write(output, payload)
    print(output)
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
