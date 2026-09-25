#!/usr/bin/env python3
"""Build the canonical F3-B verification/credit ledger replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.experiment_ledger import (
    ExecutiveExperimentLedger,
    build_episode_record,
    observed_episode_from_repair_row,
)

SCHEMA_VERSION = "cortex_f3b_verification_credit_replay_v1"
LEDGER_RELATIVE_PATH = Path("runs/ledger/cortex_executive_episodes.sqlite3")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _measured_executed_rows(
    path: Path,
) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    selected: list[tuple[int, dict[str, Any]]] = []
    total = 0
    for index, line in enumerate(
        path.read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        total += 1
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("executed") is not True:
            continue
        outcome = row.get("outcome")
        if not isinstance(outcome, dict):
            continue
        prediction = outcome.get("prediction")
        if not isinstance(prediction, dict):
            continue
        if outcome.get("before") is None or outcome.get("after") is None:
            continue
        if not isinstance(outcome.get("verdict"), str):
            continue
        reward = outcome.get("reward")
        if isinstance(reward, bool) or not isinstance(reward, (int, float)):
            continue
        selected.append((index, row))
    return selected, total


def build_replay(
    *,
    root: Path,
    repairs_path: Path,
    ledger_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError("F3-B canonical replay requires a clean source tree")

    source_sha = _sha256(repairs_path)
    selected, total_rows = _measured_executed_rows(repairs_path)
    records = []
    for row_index, row in selected:
        episode = observed_episode_from_repair_row(
            row,
            source=str(repairs_path.relative_to(root)),
            source_sha256=source_sha,
            row_index=row_index,
        )
        records.append(build_episode_record(episode))

    verification_matches = sum(
        1 for record in records if record.verification.matches_recorded
    )
    held = sum(
        1
        for record in records
        if record.verification.outcome.verdict == "held"
    )
    missed = sum(
        1
        for record in records
        if record.verification.outcome.verdict == "did_not_hold"
    )
    unmeasured = sum(
        1 for record in records if not record.verification.measured
    )
    credit_eligible = sum(1 for record in records if record.credit.eligible)
    reward_sum = sum(
        float(record.credit.reward)
        for record in records
        if record.credit.reward is not None
    )

    preconditions = {
        "selected_episode_count_positive": bool(records),
        "all_selected_executed": all(
            record.episode.executed for record in records
        ),
        "all_verifications_match_recorded": (
            verification_matches == len(records)
        ),
        "all_selected_credit_eligible": credit_eligible == len(records),
        "no_unmeasured_selected": unmeasured == 0,
    }

    inserted = 0
    already_present = 0
    ledger_count_before = 0
    ledger_count_after = 0
    quick_check = "not_run"
    episode_digests: dict[str, str] = {}

    if all(preconditions.values()):
        with ExecutiveExperimentLedger(ledger_path) as ledger:
            ledger_count_before = ledger.count()
            for record in records:
                result = ledger.append(record)
                if result == "inserted":
                    inserted += 1
                elif result == "already_present":
                    already_present += 1
            quick_check = ledger.quick_check()
            ledger_count_after = ledger.count()
            ids = tuple(record.episode_id for record in records)
            episode_digests = ledger.episode_digests(ids)

    expected_digests = {
        record.episode_id: record.payload_sha256 for record in records
    }
    ledger_roundtrip = episode_digests == expected_digests and bool(records)
    unique_episode_ids = len(expected_digests) == len(records)
    batch_digest = _sha256_text(_canonical_json(expected_digests))

    checks = {
        **preconditions,
        "ledger_quick_check_ok": quick_check == "ok",
        "ledger_roundtrip_matches": ledger_roundtrip,
        "episode_ids_unique": unique_episode_ids,
        "ledger_count_monotonic": ledger_count_after >= ledger_count_before,
        "all_selected_persisted": (
            inserted + already_present == len(records)
            and ledger_roundtrip
        ),
    }
    status = "pass" if all(checks.values()) else "fail"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_id": (
            "cortex-f3b-credit-"
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
            "path": str(repairs_path.relative_to(root)),
            "sha256": source_sha,
            "total_nonempty_rows": total_rows,
            "selection": (
                "executed=true + prediction + before/after + verdict + "
                "numeric reward"
            ),
        },
        "verification": {
            "selected_episode_count": len(records),
            "matches_recorded": verification_matches,
            "held": held,
            "missed": missed,
            "unmeasured": unmeasured,
        },
        "credit": {
            "eligible": credit_eligible,
            "ineligible": len(records) - credit_eligible,
            "reward_sum": reward_sum,
            "mean_reward": (
                reward_sum / credit_eligible if credit_eligible else None
            ),
        },
        "ledger": {
            "path": str(ledger_path.relative_to(root)),
            "quick_check": quick_check,
            "count_before": ledger_count_before,
            "count_after": ledger_count_after,
            "inserted": inserted,
            "already_present": already_present,
            "episode_count": len(records),
            "episode_digests": expected_digests,
            "batch_digest": batch_digest,
        },
        "checks": checks,
        "claim_boundary": {
            "proves": [
                "executed historical outcomes can be independently reverified",
                "unmeasured outcomes are not converted into zero reward",
                "credit is fail-closed on execution and verification consistency",
                "verified executive episodes persist idempotently across restarts",
            ],
            "does_not_prove": [
                "learned policy superiority",
                "negative-class historical calibration",
                "counterfactual outcome correctness",
                "paired shadow comparison against the legacy runner",
                "live executive authority",
                "F3 Exit Gate completion",
            ],
        },
    }


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(
            f"canonical F3-B artifact already exists: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/srv/factorio-ai-lab"),
    )
    parser.add_argument(
        "--repairs",
        type=Path,
        default=Path("runs/repairs.jsonl"),
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=LEDGER_RELATIVE_PATH,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "runs/audits/cortex_f3b_verification_credit_replay.json"
        ),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    repairs = args.repairs if args.repairs.is_absolute() else root / args.repairs
    ledger = args.ledger if args.ledger.is_absolute() else root / args.ledger
    output = args.output if args.output.is_absolute() else root / args.output

    payload = build_replay(
        root=root,
        repairs_path=repairs,
        ledger_path=ledger,
        revision=_git_revision(root),
    )
    _write_artifact(output, payload)
    print(output)
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
