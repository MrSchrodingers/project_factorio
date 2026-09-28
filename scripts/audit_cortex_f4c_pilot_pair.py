#!/usr/bin/env python3
"""Audit one canonical F4-C pilot pair before any later pilot may run."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_harness import (
    HarnessBudget,
    recompute_pair_delta,
)
from factorio_ai_lab.cortex.causal_pilot_runtime import (
    pilot_arm_order,
    pilot_task_from_manifest,
)
from factorio_ai_lab.cortex.causal_protocol import (
    MEMORY_ABLATED,
    MEMORY_ON,
    task_fingerprint,
    validate_protocol,
)
from factorio_ai_lab.cortex.memory_retrieval import memory_database_snapshot

SCHEMA_VERSION = "cortex_f4c_pilot_pair_review_v1"
DEFAULT_ROOT = Path("/srv/factorio-ai-lab")
DEFAULT_MANIFEST = Path("configs/cortex_f4c_causal_ablation_v1.json")
DEFAULT_MEMORY = Path("runs/ledger/cortex_cognitive_memory.sqlite3")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def build_review(
    *,
    root: Path,
    manifest_path: Path,
    memory_path: Path,
    seed: int,
    current_memory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = _load(manifest_path)
    protocol = validate_protocol(manifest)
    task = pilot_task_from_manifest(manifest, seed)
    expected_first, expected_second = pilot_arm_order(manifest, task)
    pair_path = root / "runs" / "f4c_pilot" / str(seed) / "pair.json"
    if not pair_path.exists():
        raise FileNotFoundError(f"pilot pair artifact missing: {pair_path}")
    artifact = _load(pair_path)
    pair = artifact.get("pair")
    if not isinstance(pair, dict):
        raise TypeError("pilot artifact pair payload missing")
    arms = pair.get("arms")
    if not isinstance(arms, dict):
        arms = {}
    on = arms.get(MEMORY_ON)
    off = arms.get(MEMORY_ABLATED)
    if not isinstance(on, dict):
        on = {}
    if not isinstance(off, dict):
        off = {}

    expected_budget = HarnessBudget.from_manifest(manifest).to_dict()
    memory_snapshot = (
        dict(current_memory)
        if current_memory is not None
        else memory_database_snapshot(memory_path).to_dict()
    )
    source_before = pair.get("source_memory_before")
    source_after = pair.get("source_memory_after")
    expected_memory = manifest["source_memory"]

    def arm_surface(arm: Mapping[str, Any], name: str) -> Any:
        observation = arm.get("observation")
        return observation.get(name) if isinstance(observation, Mapping) else None

    def memory_payload(arm: Mapping[str, Any]) -> Mapping[str, Any]:
        value = arm.get("memory")
        return value if isinstance(value, Mapping) else {}

    on_memory = memory_payload(on)
    off_memory = memory_payload(off)
    on_retrievals = on_memory.get("retrievals")
    off_retrievals = off_memory.get("retrievals")
    if not isinstance(on_retrievals, list):
        on_retrievals = []
    if not isinstance(off_retrievals, list):
        off_retrievals = []

    checkpoint = pair.get("checkpoint_digest")
    expected_fingerprint = task_fingerprint(task)
    delta_recomputed = recompute_pair_delta(pair)
    checks = {
        "artifact_schema_and_status": (
            artifact.get("schema_version") == "cortex_f4c_pilot_pair_v1"
            and artifact.get("status") == "completed"
        ),
        "pilot_identity_matches_frozen_task": (
            artifact.get("seed") == seed
            and artifact.get("task_id") == task["task_id"]
            and artifact.get("family") == task["family"]
            and artifact.get("task_fingerprint") == expected_fingerprint
            and pair.get("task_id") == task["task_id"]
            and pair.get("task_fingerprint") == expected_fingerprint
        ),
        "protocol_hashes_match_frozen_v1": (
            artifact.get("protocol_id") == manifest["protocol_id"]
            and artifact.get("protocol_manifest_sha256")
            == protocol["manifest_sha256"]
            and artifact.get("protocol_manifest_file_sha256")
            == _sha256(manifest_path)
        ),
        "code_revision_clean": (
            isinstance(artifact.get("code_revision"), dict)
            and artifact["code_revision"].get("dirty") is False
            and bool(artifact["code_revision"].get("commit"))
        ),
        "arm_order_matches_preregistered_pilot_counterbalance": (
            artifact.get("first_condition") == expected_first
            and artifact.get("second_condition") == expected_second
            and pair.get("first_condition") == expected_first
            and pair.get("second_condition") == expected_second
        ),
        "pair_valid_without_technical_invalidity": (
            pair.get("valid") is True
            and pair.get("technical_invalidities") == []
        ),
        "checkpoint_restore_exact": (
            bool(checkpoint)
            and on.get("checkpoint_digest_at_start") == checkpoint
            and off.get("checkpoint_digest_at_start") == checkpoint
        ),
        "source_memory_unchanged_and_frozen": (
            source_before == source_after == memory_snapshot
            and isinstance(source_before, dict)
            and source_before.get("manifest_sha256")
            == expected_memory["manifest_sha256"]
            and source_before.get("item_count") == expected_memory["item_count"]
            and source_before.get("occurrence_count")
            == expected_memory["occurrence_count"]
        ),
        "candidate_tool_budget_surfaces_matched": (
            arm_surface(on, "candidate_surface")
            == arm_surface(off, "candidate_surface")
            == task["spec"]["candidate_classes"]
            and arm_surface(on, "tool_surface")
            == arm_surface(off, "tool_surface")
            and on.get("budget") == off.get("budget") == expected_budget
        ),
        "scores_valid_and_delta_recomputable": (
            isinstance(on.get("score"), dict)
            and isinstance(off.get("score"), dict)
            and on["score"].get("valid") is True
            and off["score"].get("valid") is True
            and on["score"].get("j") is not None
            and off["score"].get("j") is not None
            and pair.get("delta_J") == delta_recomputed
        ),
        "retrieval_only_ablation_and_quarantine": (
            len(on_retrievals) == 1
            and len(off_retrievals) == 1
            and bool(on_retrievals[0].get("result", {}).get("results"))
            and off_retrievals[0].get("result", {}).get("results") == []
            and on_memory.get("source_write_attempted") is False
            and off_memory.get("source_write_attempted") is False
            and len(on_memory.get("quarantined_writes") or []) == 1
            and len(off_memory.get("quarantined_writes") or []) == 1
        ),
        "no_live_factorio_authority": (
            artifact.get("live_factorio_world") is False
            and artifact.get("factorio_rcon_used") is False
            and artifact.get("fle_environment_created") is False
            and artifact.get("world_lease_acquired") is False
            and artifact.get("automatic_retry") is False
        ),
        "pilot_only_claim_boundary": (
            isinstance(artifact.get("claim_boundary"), dict)
            and artifact["claim_boundary"].get("pilot_only") is True
            and artifact["claim_boundary"].get(
                "excluded_from_primary_f4c_inference"
            )
            is True
            and artifact["claim_boundary"].get(
                "evaluation_seed_executed"
            )
            is False
            and artifact["claim_boundary"].get(
                "confirmatory_seed_executed"
            )
            is False
        ),
    }
    status = "pass" if all(checks.values()) else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "seed": seed,
        "task_id": task["task_id"],
        "family": task["family"],
        "pair_artifact": str(pair_path),
        "pair_artifact_sha256": _sha256(pair_path),
        "code_revision": artifact.get("code_revision"),
        "delta_J": pair.get("delta_J"),
        "scores": {
            MEMORY_ON: on.get("score"),
            MEMORY_ABLATED: off.get("score"),
        },
        "checks": checks,
        "claim_boundary": {
            "instrumentation_only": True,
            "variance_adaptation_forbidden": True,
            "primary_f4c_inference": False,
            "evaluation_partition_untouched": True,
            "confirmatory_partition_untouched": True,
        },
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"pilot review already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--memory", type=Path, default=DEFAULT_MEMORY)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = args.manifest if args.manifest.is_absolute() else root / args.manifest
    memory = args.memory if args.memory.is_absolute() else root / args.memory
    review_path = root / "runs" / "f4c_pilot" / str(args.seed) / "review.json"
    payload = build_review(
        root=root,
        manifest_path=manifest,
        memory_path=memory,
        seed=args.seed,
    )
    _write(review_path, payload)
    print(review_path)
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
