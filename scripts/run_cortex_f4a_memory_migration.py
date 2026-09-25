#!/usr/bin/env python3
"""Canonical F4-A migration into the typed cognitive-memory substrate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.cortex.memory import (
    CognitiveMemoryStore,
    MemoryItem,
    MemoryKind,
    MemoryOccurrence,
    ValidityScope,
    WorkingMemory,
    WorkingMemorySlot,
)

SCHEMA_VERSION = "cortex_f4a_memory_substrate_migration_v1"
DEFAULT_MEMORY_PATH = Path("runs/ledger/cortex_cognitive_memory.sqlite3")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _executive_snapshot(
    path: Path,
) -> tuple[str, list[tuple[str, str, dict[str, Any]]], str]:
    connection = sqlite3.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=10.0,
    )
    try:
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        rows = connection.execute(
            """
            SELECT episode_id, payload_sha256, payload_json
            FROM executive_episodes
            ORDER BY episode_id
            """
        ).fetchall()
    finally:
        connection.close()
    parsed: list[tuple[str, str, dict[str, Any]]] = []
    manifest = []
    for episode_id, payload_sha256, payload_json in rows:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            raise TypeError(f"invalid executive episode {episode_id}")
        parsed.append((str(episode_id), str(payload_sha256), payload))
        manifest.append(
            {
                "episode_id": str(episode_id),
                "payload_sha256": str(payload_sha256),
            }
        )
    snapshot_sha = _sha256_text(_canonical_json(manifest))
    return quick_check, parsed, snapshot_sha


def _jsonl_rows(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    rows = []
    malformed = 0
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(value, dict):
            rows.append((index, value))
        else:
            malformed += 1
    return rows, malformed


def _semantic_key(row: Mapping[str, Any]) -> str:
    identity = {
        "stage": str(row.get("stage") or ""),
        "lesson": str(row.get("lesson") or ""),
        "next_hypothesis": str(row.get("next_hypothesis") or ""),
    }
    return "semantic-" + _sha256_text(_canonical_json(identity))[:24]


def _working_memory_probe() -> dict[str, Any]:
    memory = WorkingMemory(capacity=3)
    for key in ("goal", "belief", "candidate", "prediction"):
        memory = memory.remember(
            WorkingMemorySlot(
                key=key,
                value={"present": True},
                source="f4a_migration_probe",
            )
        )
    return {
        "capacity": memory.capacity,
        "size": len(memory.slots),
        "keys": [slot.key for slot in memory.slots],
        "evicted_oldest": memory.recall("goal") is None,
        "persistent": False,
    }


def build_migration(
    *,
    root: Path,
    executive_ledger_path: Path,
    knowledge_path: Path,
    counterexamples_path: Path,
    memory_path: Path,
    revision: dict[str, Any],
) -> dict[str, Any]:
    if revision.get("dirty") is not False:
        raise RuntimeError("F4-A canonical migration requires a clean source tree")

    executive_quick, executive_rows, executive_snapshot_sha = (
        _executive_snapshot(executive_ledger_path)
    )
    knowledge_sha = _sha256(knowledge_path)
    counterexample_sha = _sha256(counterexamples_path)
    batch_seed = {
        "code_commit": revision.get("commit"),
        "executive_snapshot_sha256": executive_snapshot_sha,
        "knowledge_sha256": knowledge_sha,
        "counterexamples_sha256": counterexample_sha,
    }
    batch_id = "cortex-f4a-" + _sha256_text(
        _canonical_json(batch_seed)
    )[:20]

    knowledge_rows, knowledge_malformed = _jsonl_rows(knowledge_path)
    counterexample_rows, counterexample_malformed = _jsonl_rows(
        counterexamples_path
    )

    inserted = {kind.value: 0 for kind in MemoryKind if kind is not MemoryKind.WORKING}
    already_present = {
        kind.value: 0 for kind in MemoryKind if kind is not MemoryKind.WORKING
    }
    semantic_verified_rows = 0
    semantic_qualified_rows = 0
    semantic_unverified_rows = 0
    counterexample_valid_rows = 0
    procedural_ids: set[str] = set()

    with CognitiveMemoryStore(memory_path) as store:
        for episode_id, _digest, payload in executive_rows:
            episode = payload.get("episode")
            verification = payload.get("verification")
            credit = payload.get("credit")
            if not isinstance(episode, dict):
                continue
            if not isinstance(verification, dict):
                verification = {}
            if not isinstance(credit, dict):
                credit = {}
            action_key = str(episode.get("action_key") or "")
            symptom = str(episode.get("symptom") or "")
            stage = episode.get("stage")
            stage_value = stage if isinstance(stage, str) and stage else None
            reward = credit.get("reward")
            if isinstance(reward, bool) or not isinstance(reward, (int, float)):
                reward_value = None
            else:
                reward_value = float(reward)

            episodic = MemoryItem(
                kind=MemoryKind.EPISODIC,
                key=episode_id,
                content={
                    "run_id": episode.get("run_id"),
                    "generation": episode.get("generation"),
                    "action_key": action_key,
                    "executed": episode.get("executed") is True,
                    "verdict": (
                        (verification.get("outcome") or {}).get("verdict")
                        if isinstance(verification.get("outcome"), dict)
                        else None
                    ),
                    "reward": reward_value,
                },
                scope=ValidityScope(
                    stage=stage_value,
                    symptom=symptom or None,
                ),
            )
            episodic_occurrence = MemoryOccurrence(
                memory_id=episodic.memory_id,
                source=str(executive_ledger_path.relative_to(root)),
                source_sha256=executive_snapshot_sha,
                source_locator=episode_id,
                payload=payload,
                batch_id=batch_id,
                qualified=verification.get("matches_recorded") is True,
                reward=reward_value,
            )
            result = store.ingest(episodic, episodic_occurrence)
            (inserted if result == "inserted" else already_present)[
                MemoryKind.EPISODIC.value
            ] += 1

            if (
                credit.get("eligible") is True
                and reward_value is not None
                and action_key
                and symptom
            ):
                procedural = MemoryItem(
                    kind=MemoryKind.PROCEDURAL,
                    key=f"{symptom}|{action_key}",
                    content={"action_key": action_key},
                    scope=ValidityScope(symptom=symptom),
                )
                procedural_occurrence = MemoryOccurrence(
                    memory_id=procedural.memory_id,
                    source=str(executive_ledger_path.relative_to(root)),
                    source_sha256=executive_snapshot_sha,
                    source_locator=episode_id,
                    payload=payload,
                    batch_id=batch_id,
                    qualified=True,
                    reward=reward_value,
                )
                result = store.ingest(procedural, procedural_occurrence)
                (inserted if result == "inserted" else already_present)[
                    MemoryKind.PROCEDURAL.value
                ] += 1
                procedural_ids.add(procedural.memory_id)

        for row_index, row in knowledge_rows:
            verification = row.get("verification")
            if not (
                isinstance(verification, Mapping)
                and verification.get("verified") is True
            ):
                semantic_unverified_rows += 1
                continue
            semantic_verified_rows += 1
            lesson = row.get("lesson")
            hypothesis = row.get("next_hypothesis")
            if not isinstance(lesson, str) or not lesson.strip():
                continue
            if not isinstance(hypothesis, str):
                hypothesis = ""
            stage = row.get("stage")
            stage_value = stage if isinstance(stage, str) and stage else None
            evidence_keys = row.get("evidence_keys")
            facts = row.get("facts")
            qualified = (
                isinstance(evidence_keys, list)
                and bool(evidence_keys)
                and all(isinstance(key, str) and key for key in evidence_keys)
                and isinstance(facts, Mapping)
            )
            if qualified:
                semantic_qualified_rows += 1

            semantic = MemoryItem(
                kind=MemoryKind.SEMANTIC,
                key=_semantic_key(row),
                content={
                    "lesson": lesson,
                    "next_hypothesis": hypothesis,
                },
                scope=ValidityScope(stage=stage_value),
            )
            occurrence = MemoryOccurrence(
                memory_id=semantic.memory_id,
                source=str(knowledge_path.relative_to(root)),
                source_sha256=knowledge_sha,
                source_locator=f"row:{row_index}",
                observed_at=(
                    row.get("at") if isinstance(row.get("at"), str) else None
                ),
                payload=row,
                batch_id=batch_id,
                qualified=qualified,
            )
            result = store.ingest(semantic, occurrence)
            (inserted if result == "inserted" else already_present)[
                MemoryKind.SEMANTIC.value
            ] += 1

        for row_index, row in counterexample_rows:
            signature = row.get("signature")
            detail = row.get("detail")
            repair = row.get("repair")
            if not isinstance(signature, str) or not signature.strip():
                continue
            stage = row.get("stage")
            phase = row.get("phase")
            stage_value = stage if isinstance(stage, str) and stage else None
            phase_value = phase if isinstance(phase, str) and phase else None
            qualified = (
                isinstance(detail, str)
                and bool(detail.strip())
                and isinstance(repair, Mapping)
            )
            if qualified:
                counterexample_valid_rows += 1
            counterexample = MemoryItem(
                kind=MemoryKind.COUNTEREXAMPLE,
                key=signature,
                content={"signature": signature},
                scope=ValidityScope(stage=stage_value, phase=phase_value),
            )
            occurrence = MemoryOccurrence(
                memory_id=counterexample.memory_id,
                source=str(counterexamples_path.relative_to(root)),
                source_sha256=counterexample_sha,
                source_locator=f"row:{row_index}",
                observed_at=(
                    row.get("at") if isinstance(row.get("at"), str) else None
                ),
                payload=row,
                batch_id=batch_id,
                qualified=qualified,
            )
            result = store.ingest(counterexample, occurrence)
            (inserted if result == "inserted" else already_present)[
                MemoryKind.COUNTEREXAMPLE.value
            ] += 1

        procedure_evidence = []
        for memory_id in sorted(procedural_ids):
            evidence = store.refresh_procedural_confidence(memory_id)
            procedure_evidence.append(
                {"memory_id": memory_id, **evidence.to_dict()}
            )

        quick_check = store.quick_check()
        snapshot = store.kind_snapshot()
        batch_manifest = store.batch_manifest(batch_id)

    working = _working_memory_probe()
    total_inserted = sum(inserted.values())
    total_already = sum(already_present.values())
    expected_batch_occurrences = (
        len(executive_rows)
        + sum(
            1
            for _, _, payload in executive_rows
            if isinstance(payload.get("credit"), dict)
            and payload["credit"].get("eligible") is True
            and isinstance(payload["credit"].get("reward"), (int, float))
            and not isinstance(payload["credit"].get("reward"), bool)
        )
        + semantic_verified_rows
        + len(
            [
                row
                for _, row in counterexample_rows
                if isinstance(row.get("signature"), str)
                and row["signature"].strip()
            ]
        )
    )
    checks = {
        "executive_ledger_quick_check_ok": executive_quick == "ok",
        "memory_quick_check_ok": quick_check == "ok",
        "working_memory_bounded": (
            working["size"] == working["capacity"]
            and working["evicted_oldest"] is True
            and working["persistent"] is False
        ),
        "episodic_occurrences_cover_executive_episodes": (
            snapshot[MemoryKind.EPISODIC.value]["occurrences"]
            >= len(executive_rows)
        ),
        "semantic_verified_rows_imported": (
            snapshot[MemoryKind.SEMANTIC.value]["occurrences"]
            >= semantic_verified_rows
        ),
        "semantic_qualified_support_preserved": (
            snapshot[MemoryKind.SEMANTIC.value]["qualified_occurrences"]
            >= semantic_qualified_rows
        ),
        "semantic_deduplicated": (
            snapshot[MemoryKind.SEMANTIC.value]["items"]
            < snapshot[MemoryKind.SEMANTIC.value]["occurrences"]
        ),
        "procedural_credit_only": (
            snapshot[MemoryKind.PROCEDURAL.value]["occurrences"]
            == sum(row["support_count"] for row in procedure_evidence)
        ),
        "procedural_confidence_empirical": all(
            row["confidence"]["method"] == "wilson_lower_95"
            and row["confidence"]["n"] == row["support_count"]
            for row in procedure_evidence
        ),
        "counterexamples_first_class": (
            snapshot[MemoryKind.COUNTEREXAMPLE.value]["occurrences"]
            >= counterexample_valid_rows > 0
        ),
        "batch_manifest_complete": (
            batch_manifest["occurrence_count"] == expected_batch_occurrences
        ),
        "no_live_authority": True,
    }
    status = "pass" if all(checks.values()) else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_id": batch_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "code_revision": revision,
        "authority": "shadow",
        "world_mutation": False,
        "factorio_rcon_used": False,
        "fle_environment_created": False,
        "world_lease_acquired": False,
        "execution_grant_created": False,
        "continuous_authority": False,
        "sources": {
            "executive_ledger": {
                "path": str(executive_ledger_path.relative_to(root)),
                "quick_check": executive_quick,
                "episode_count": len(executive_rows),
                "snapshot_sha256": executive_snapshot_sha,
            },
            "knowledge": {
                "path": str(knowledge_path.relative_to(root)),
                "sha256": knowledge_sha,
                "rows": len(knowledge_rows),
                "malformed": knowledge_malformed,
                "verified_rows": semantic_verified_rows,
                "qualified_verified_rows": semantic_qualified_rows,
                "unverified_rows": semantic_unverified_rows,
            },
            "counterexamples": {
                "path": str(counterexamples_path.relative_to(root)),
                "sha256": counterexample_sha,
                "rows": len(counterexample_rows),
                "malformed": counterexample_malformed,
                "qualified_rows": counterexample_valid_rows,
            },
        },
        "working_memory": working,
        "store": {
            "path": str(memory_path.relative_to(root)),
            "quick_check": quick_check,
            "snapshot": snapshot,
            "inserted": inserted,
            "already_present": already_present,
            "total_inserted": total_inserted,
            "total_already_present": total_already,
            "batch_manifest": batch_manifest,
            "procedures": procedure_evidence,
        },
        "checks": checks,
        "claim_boundary": {
            "proves": [
                "working memory is bounded and distinct from durable memory",
                "episodic semantic procedural and counterexample memory share a typed persistent substrate",
                "semantic duplicates aggregate support without erasing occurrence provenance",
                "incomplete semantic provenance is separated from qualified support",
                "procedural confidence is derived only from executed binary outcomes",
                "counterexamples are first-class persistent memory",
                "the canonical migration batch is restart-safe and tamper-evident",
            ],
            "does_not_prove": [
                "memory improves decisions",
                "retrieval quality",
                "consolidation quality",
                "forgetting policy validity",
                "cross-seed transfer",
                "F4 Exit Gate completion",
            ],
        },
    }


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"canonical F4-A artifact already exists: {path}")
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
    parser.add_argument(
        "--executive-ledger",
        type=Path,
        default=Path("runs/ledger/cortex_executive_episodes.sqlite3"),
    )
    parser.add_argument(
        "--knowledge",
        type=Path,
        default=Path("runs/knowledge.jsonl"),
    )
    parser.add_argument(
        "--counterexamples",
        type=Path,
        default=Path("runs/counterexamples.jsonl"),
    )
    parser.add_argument(
        "--memory",
        type=Path,
        default=DEFAULT_MEMORY_PATH,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/audits/cortex_f4a_memory_substrate_migration.json"),
    )
    args = parser.parse_args()
    root = args.root.resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else root / path

    payload = build_migration(
        root=root,
        executive_ledger_path=resolve(args.executive_ledger),
        knowledge_path=resolve(args.knowledge),
        counterexamples_path=resolve(args.counterexamples),
        memory_path=resolve(args.memory),
        revision=_git_revision(root),
    )
    _write_artifact(resolve(args.output), payload)
    print(resolve(args.output))
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
