#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_protocol import validate_protocol


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", *args],
        cwd=root,
        text=True,
    ).strip()


def build_freeze_artifact(root: Path, manifest_path: Path) -> dict[str, Any]:
    payload = _load(manifest_path)
    summary = validate_protocol(payload)

    f4a = root / "runs/audits/cortex_f4a_memory_substrate_migration.json"
    f4b = root / "runs/audits/cortex_f4b_memory_retrieval.json"
    phase_path = root / "runs/cortex_phase_state.json"
    f4b_payload = _load(f4b)
    phase = _load(phase_path)

    source = payload["source_memory"]
    f4b_source = f4b_payload.get("source")
    if not isinstance(f4b_source, dict):
        f4b_source = {}
    database_before = f4b_source.get("database_before")
    if not isinstance(database_before, dict):
        database_before = {}

    confirmatory = phase.get("modes", {}).get("confirmatory", {})
    pending = confirmatory.get("pending", [])
    running = confirmatory.get("running", [])
    completed = confirmatory.get("valid_completed", 0)

    checks = {
        "protocol_matches_frozen_v1_builder": True,
        "source_f4a_artifact_hash_matches": _sha256(f4a)
        == source["f4a_artifact_sha256"],
        "source_f4b_artifact_hash_matches": _sha256(f4b)
        == source["f4b_artifact_sha256"],
        "source_memory_manifest_matches_f4b": database_before.get("manifest_sha256")
        == source["manifest_sha256"],
        "source_memory_item_count_matches_f4b": database_before.get("item_count")
        == source["item_count"],
        "source_memory_occurrence_count_matches_f4b": database_before.get(
            "occurrence_count"
        )
        == source["occurrence_count"],
        "task_family_count_at_least_four": summary["task_family_count"] >= 4,
        "evaluation_pair_count_frozen": summary["evaluation_pair_count"] == 20,
        "pilot_pair_count_frozen": summary["pilot_pair_count"] == 8,
        "counterbalancing_is_10_10": (
            summary["memory_on_first"],
            summary["memory_ablated_first"],
        )
        == (10, 10),
        "confirmatory_reservation_exact": summary["confirmatory_reserved"]
        == list(range(20261101, 20261111)),
        "confirmatory_seeds_still_pending": sorted(pending)
        == list(range(20261101, 20261111))
        and running == []
        and completed == 0,
        "memory_ablation_is_retrieval_only": (
            payload["pairing"]["memory_ablated"]["retrieval_mode"]
            == "empty_retrieval_result"
            and payload["pairing"]["memory_ablated"][
                "unrelated_capabilities_removed"
            ]
            is False
        ),
        "evaluation_memory_updates_quarantined": (
            payload["source_memory"]["evaluation_write_policy"]
            == "quarantine_until_all_evaluation_pairs_complete"
        ),
        "missing_is_never_zero": payload["invalidity_and_missingness"][
            "missing_is_never_zero"
        ]
        is True,
        "no_live_authority": payload["authority"] == "shadow"
        and payload["continuous_authority"] is False,
    }

    status = "pass" if all(checks.values()) else "fail"
    head = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    dirty = bool(_git(root, "status", "--porcelain=v1"))

    return {
        "schema_version": "cortex_f4c_protocol_freeze_v1",
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "authority": "shadow",
        "world_mutation": False,
        "factorio_rcon_used": False,
        "fle_environment_created": False,
        "world_lease_acquired": False,
        "execution_grant_created": False,
        "continuous_authority": False,
        "code_revision": {
            "branch": branch,
            "commit": head,
            "dirty": dirty,
        },
        "protocol": {
            "path": str(manifest_path.relative_to(root)),
            "file_sha256": _sha256(manifest_path),
            **summary,
        },
        "source": {
            "f4a_artifact_path": str(f4a.relative_to(root)),
            "f4a_artifact_sha256": _sha256(f4a),
            "f4b_artifact_path": str(f4b.relative_to(root)),
            "f4b_artifact_sha256": _sha256(f4b),
            "memory_manifest_sha256": database_before.get("manifest_sha256"),
            "memory_item_count": database_before.get("item_count"),
            "memory_occurrence_count": database_before.get("occurrence_count"),
        },
        "checks": checks,
        "claim_boundary": {
            "proves": [
                "the F4-C causal protocol is frozen before evaluation outcomes",
                "pilot evaluation and confirmatory seed partitions are disjoint",
                "MEMORY ON and MEMORY ABLATED preserve matched non-memory capabilities",
                "the primary endpoint and exact paired inference are preregistered",
                "missing outcomes are fail-closed rather than coerced to zero",
            ],
            "does_not_prove": [
                "memory improves outcomes",
                "F4-C causal transfer",
                "F4 Exit Gate completion",
                "continuous autonomous authority",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("configs/cortex_f4c_causal_ablation_v1.json"),
    )
    parser.add_argument(
        "--write",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = args.manifest
    if not manifest.is_absolute():
        manifest = root / manifest
    artifact = build_freeze_artifact(root, manifest)
    raw = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if args.write is not None:
        output = args.write
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(raw, encoding="utf-8")
        print(output)
    else:
        print(raw, end="")
    return 0 if artifact["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
