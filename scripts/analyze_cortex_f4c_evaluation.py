#!/usr/bin/env python3
"""Compute the preregistered F4-C held-out primary inference once."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.causal_inference import summarize_primary_inference
from factorio_ai_lab.cortex.causal_protocol import validate_protocol

SCHEMA_VERSION = "cortex_f4c_evaluation_inference_v1"
DEFAULT_ROOT = Path("/srv/factorio-ai-lab")
DEFAULT_MANIFEST = Path("configs/cortex_f4c_causal_ablation_v1.json")
DEFAULT_OUTPUT = Path("runs/audits/cortex_f4c_evaluation_inference.json")

FROZEN_TREATMENT_SHA256 = (
    "d412ed9af1cfa75def10338a6dfbec97c60cf4ce792645b6d38747c3f47d3469"
)
FROZEN_RUNTIME_SHA256 = (
    "0d64659a8c932592c10b9af9476cfa3cac1af0e2a54ee5953febac295094e085"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def build_analysis(
    *,
    root: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest = _load(manifest_path)
    protocol = validate_protocol(manifest)
    seeds = [
        int(value) for value in manifest["seed_partitions"]["evaluation"]
    ]
    families = tuple(
        dict.fromkeys(
            str(task["family"])
            for task in manifest["task_design"]["evaluation_tasks"]
        )
    )
    rows: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    hard_failures: list[int] = []
    technical_invalid: list[dict[str, Any]] = []

    for seed in seeds:
        pair_path = root / "runs/f4c_evaluation" / str(seed) / "pair.json"
        review_path = (
            root / "runs/f4c_evaluation" / str(seed) / "review.json"
        )
        if not pair_path.exists() or not review_path.exists():
            raise RuntimeError(
                f"evaluation sequence incomplete at seed {seed}"
            )
        review = _load(review_path)
        if review.get("seed") != seed:
            raise RuntimeError(f"evaluation review seed mismatch: {seed}")
        status = review.get("status")
        if status == "fail":
            hard_failures.append(seed)
        elif status == "technical_invalid":
            technical_invalid.append(
                {
                    "seed": seed,
                    "family": review.get("family"),
                    "reasons": review.get("technical_invalidities"),
                }
            )
        elif status != "pass":
            raise RuntimeError(
                f"evaluation review has unknown status at seed {seed}"
            )
        rows.append(
            {
                "seed": seed,
                "family": review.get("family"),
                "status": status,
                "valid_for_primary_inference": (
                    review.get("valid_for_primary_inference") is True
                ),
                "delta_J": review.get("delta_J"),
            }
        )
        provenance.append(
            {
                "seed": seed,
                "pair_sha256": _sha256(pair_path),
                "review_sha256": _sha256(review_path),
                "review_status": status,
            }
        )

    if hard_failures:
        raise RuntimeError(
            "integrity-failing evaluation reviews block inference: "
            + ",".join(map(str, hard_failures))
        )

    valid_rows = [
        row for row in rows if row["valid_for_primary_inference"]
    ]
    stats = manifest["statistics"]
    if valid_rows:
        inference = summarize_primary_inference(
            valid_rows,
            expected_families=families,
            alpha=float(stats["alpha"]),
            sesoi=float(
                stats["minimum_practically_relevant_effect_delta_J"]
            ),
            minimum_valid_pairs=int(stats["minimum_analyzable_pairs"]),
            minimum_per_family=int(
                stats["minimum_analyzable_pairs_per_family"]
            ),
        )
    else:
        inference = {
            "valid_pair_count": 0,
            "family_valid_counts": {family: 0 for family in families},
            "sample_sufficient": False,
            "mean_delta_J": None,
            "median_delta_J": None,
            "paired_cohens_dz": None,
            "one_sided_exact_p": None,
            "confidence_set_95pct": None,
            "alpha": float(stats["alpha"]),
            "sesoi_delta_J": float(
                stats["minimum_practically_relevant_effect_delta_J"]
            ),
            "minimum_valid_pairs": int(stats["minimum_analyzable_pairs"]),
            "minimum_per_family": int(
                stats["minimum_analyzable_pairs_per_family"]
            ),
            "positive_causal_memory_result": False,
            "decision": "inconclusive",
        }

    treatment_path = (
        root / "src/factorio_ai_lab/cortex/causal_treatment.py"
    )
    runtime_path = (
        root / "src/factorio_ai_lab/cortex/causal_pilot_runtime.py"
    )
    if _sha256(treatment_path) != FROZEN_TREATMENT_SHA256:
        raise RuntimeError("treatment semantics changed before inference")
    if _sha256(runtime_path) != FROZEN_RUNTIME_SHA256:
        raise RuntimeError("task runtime changed before inference")

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "computed_at": datetime.now(UTC).isoformat(),
        "protocol": {
            "protocol_id": manifest["protocol_id"],
            "manifest_file_sha256": _sha256(manifest_path),
            "manifest_sha256": protocol["manifest_sha256"],
        },
        "semantic_lock": {
            "treatment_module_sha256": FROZEN_TREATMENT_SHA256,
            "pilot_runtime_sha256": FROZEN_RUNTIME_SHA256,
        },
        "evaluation_pair_count": len(seeds),
        "review_rows": rows,
        "technical_invalid_pairs": technical_invalid,
        "provenance": provenance,
        "primary_inference": inference,
        "claim_boundary": {
            "pilot_rows_included": False,
            "confirmatory_rows_included": False,
            "outcome_dependent_exclusion": False,
            "replacement_seeds_used": False,
            "fixed_sample_stopping_rule": True,
        },
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"evaluation inference already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = (
        args.manifest if args.manifest.is_absolute() else root / args.manifest
    )
    output = args.output if args.output.is_absolute() else root / args.output
    payload = build_analysis(root=root, manifest_path=manifest)
    _write(output, payload)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
