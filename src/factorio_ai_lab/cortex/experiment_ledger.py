"""Persistent F3 executive verification and credit ledger.

The ledger stores only explicit observed episodes. It never assigns credit to
an unexecuted counterfactual and never converts an unmeasured outcome into a
zero reward.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from factorio_ai_lab.learning.repair_loop import (
    Prediction,
    PredictionOutcome,
    evaluate_prediction,
)

LEDGER_SCHEMA_VERSION = "cortex_executive_episode_ledger_v1"
CREDIT_VERIFIED = "verified_measured_outcome"
CREDIT_NOT_EXECUTED = "not_executed"
CREDIT_UNMEASURED = "outcome_unmeasured"
CREDIT_RECORDED_MISMATCH = "recorded_outcome_mismatch"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _metric_payload(metric: str, value: Any) -> dict[str, Any]:
    current: Any = value
    for segment in reversed(metric.split(".")):
        current = {segment: current}
    return current


@dataclass(frozen=True)
class ObservedExecutiveEpisode:
    """One observed repair row normalized for executive evaluation."""

    source: str
    source_sha256: str
    row_index: int
    run_id: str
    generation: int | None
    stage: str | None
    symptom: str
    action_key: str
    executed: bool
    prediction: Prediction
    before: Any
    after: Any
    recorded_verdict: str | None
    recorded_reward: float | None
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("episode source must be non-empty")
        if not self.source_sha256:
            raise ValueError("episode source_sha256 must be non-empty")
        if self.row_index < 0:
            raise ValueError("episode row_index cannot be negative")
        if not self.run_id:
            raise ValueError("episode run_id must be non-empty")
        if not self.symptom:
            raise ValueError("episode symptom must be non-empty")
        if not self.action_key:
            raise ValueError("episode action_key must be non-empty")
        object.__setattr__(self, "targets", tuple(self.targets))

    @property
    def episode_id(self) -> str:
        identity = {
            "source": self.source,
            "row_index": self.row_index,
            "run_id": self.run_id,
            "generation": self.generation,
            "stage": self.stage,
            "symptom": self.symptom,
            "action_key": self.action_key,
            "executed": self.executed,
            "prediction": self.prediction.to_dict(),
            "before": self.before,
            "after": self.after,
            "targets": list(self.targets),
        }
        return "episode-" + _sha256_text(_canonical_json(identity))[:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "row_index": self.row_index,
            "run_id": self.run_id,
            "generation": self.generation,
            "stage": self.stage,
            "symptom": self.symptom,
            "action_key": self.action_key,
            "executed": self.executed,
            "prediction": self.prediction.to_dict(),
            "before": self.before,
            "after": self.after,
            "recorded_verdict": self.recorded_verdict,
            "recorded_reward": self.recorded_reward,
            "targets": list(self.targets),
        }


@dataclass(frozen=True)
class ExecutiveVerification:
    """Recomputed prediction outcome plus agreement with the source record."""

    episode_id: str
    outcome: PredictionOutcome
    recorded_verdict: str | None
    recorded_reward: float | None
    matches_recorded: bool

    @property
    def measured(self) -> bool:
        return self.outcome.reward is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "outcome": self.outcome.to_dict(),
            "recorded_verdict": self.recorded_verdict,
            "recorded_reward": self.recorded_reward,
            "matches_recorded": self.matches_recorded,
            "measured": self.measured,
        }


@dataclass(frozen=True)
class CreditAssignment:
    """Credit granted only to executed, measured, internally consistent evidence."""

    episode_id: str
    action_key: str
    eligible: bool
    reward: float | None
    reason: str

    def __post_init__(self) -> None:
        if self.eligible != (self.reward is not None):
            raise ValueError("eligible credit requires a numeric reward")
        if not self.reason:
            raise ValueError("credit reason must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "action_key": self.action_key,
            "eligible": self.eligible,
            "reward": self.reward,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExecutiveEpisodeRecord:
    """One immutable episode ready for persistent experiment storage."""

    episode: ObservedExecutiveEpisode
    verification: ExecutiveVerification
    credit: CreditAssignment

    def __post_init__(self) -> None:
        episode_id = self.episode.episode_id
        if self.verification.episode_id != episode_id:
            raise ValueError("verification episode_id mismatch")
        if self.credit.episode_id != episode_id:
            raise ValueError("credit episode_id mismatch")

    @property
    def episode_id(self) -> str:
        return self.episode.episode_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "cortex_executive_episode_v1",
            "episode": self.episode.to_dict(),
            "verification": self.verification.to_dict(),
            "credit": self.credit.to_dict(),
        }

    @property
    def payload_sha256(self) -> str:
        return _sha256_text(_canonical_json(self.to_dict()))


def observed_episode_from_repair_row(
    row: dict[str, Any],
    *,
    source: str,
    source_sha256: str,
    row_index: int,
) -> ObservedExecutiveEpisode:
    outcome = row.get("outcome")
    if not isinstance(outcome, dict):
        outcome = {}
    prediction_raw = outcome.get("prediction")
    if not isinstance(prediction_raw, dict):
        prediction_raw = {}
    metric = prediction_raw.get("metric")
    direction = prediction_raw.get("direction")
    if not isinstance(metric, str) or not metric:
        raise ValueError("repair row lacks prediction metric")
    if not isinstance(direction, str) or not direction:
        raise ValueError("repair row lacks prediction direction")

    run_id = row.get("run_id")
    symptom = row.get("symptom")
    action_key = row.get("action_key")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("repair row lacks run_id")
    if not isinstance(symptom, str) or not symptom:
        raise ValueError("repair row lacks symptom")
    if not isinstance(action_key, str) or not action_key:
        raise ValueError("repair row lacks action_key")

    generation = row.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool):
        generation = None
    stage = row.get("stage")
    if not isinstance(stage, str):
        stage = None
    targets_raw = row.get("targets")
    targets = (
        tuple(str(value) for value in targets_raw)
        if isinstance(targets_raw, list)
        else ()
    )
    recorded_reward = outcome.get("reward")
    if isinstance(recorded_reward, bool) or not isinstance(
        recorded_reward,
        (int, float),
    ):
        recorded_reward = None
    else:
        recorded_reward = float(recorded_reward)
    recorded_verdict = outcome.get("verdict")
    if not isinstance(recorded_verdict, str):
        recorded_verdict = None

    return ObservedExecutiveEpisode(
        source=source,
        source_sha256=source_sha256,
        row_index=row_index,
        run_id=run_id,
        generation=generation,
        stage=stage,
        symptom=symptom,
        action_key=action_key,
        executed=row.get("executed") is True,
        prediction=Prediction(metric=metric, direction=direction),
        before=outcome.get("before"),
        after=outcome.get("after"),
        recorded_verdict=recorded_verdict,
        recorded_reward=recorded_reward,
        targets=targets,
    )


def verify_observed_episode(
    episode: ObservedExecutiveEpisode,
) -> ExecutiveVerification:
    before = _metric_payload(episode.prediction.metric, episode.before)
    after = _metric_payload(episode.prediction.metric, episode.after)
    outcome = evaluate_prediction(episode.prediction, before, after)
    matches = (
        episode.recorded_verdict == outcome.verdict
        and episode.recorded_reward == outcome.reward
    )
    return ExecutiveVerification(
        episode_id=episode.episode_id,
        outcome=outcome,
        recorded_verdict=episode.recorded_verdict,
        recorded_reward=episode.recorded_reward,
        matches_recorded=matches,
    )


def assign_verified_credit(
    episode: ObservedExecutiveEpisode,
    verification: ExecutiveVerification,
) -> CreditAssignment:
    if verification.episode_id != episode.episode_id:
        raise ValueError("verification does not belong to episode")
    if not episode.executed:
        return CreditAssignment(
            episode_id=episode.episode_id,
            action_key=episode.action_key,
            eligible=False,
            reward=None,
            reason=CREDIT_NOT_EXECUTED,
        )
    if not verification.matches_recorded:
        return CreditAssignment(
            episode_id=episode.episode_id,
            action_key=episode.action_key,
            eligible=False,
            reward=None,
            reason=CREDIT_RECORDED_MISMATCH,
        )
    if verification.outcome.reward is None:
        return CreditAssignment(
            episode_id=episode.episode_id,
            action_key=episode.action_key,
            eligible=False,
            reward=None,
            reason=CREDIT_UNMEASURED,
        )
    return CreditAssignment(
        episode_id=episode.episode_id,
        action_key=episode.action_key,
        eligible=True,
        reward=float(verification.outcome.reward),
        reason=CREDIT_VERIFIED,
    )


def build_episode_record(
    episode: ObservedExecutiveEpisode,
) -> ExecutiveEpisodeRecord:
    verification = verify_observed_episode(episode)
    credit = assign_verified_credit(episode, verification)
    return ExecutiveEpisodeRecord(
        episode=episode,
        verification=verification,
        credit=credit,
    )


class EpisodeLedgerConflict(RuntimeError):
    """Raised when one episode id is reused for different immutable content."""


class ExecutiveExperimentLedger:
    """SQLite-backed persistent ledger for verified executive episodes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            str(self.path),
            timeout=30.0,
            isolation_level=None,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS executive_episodes (
                episode_id TEXT PRIMARY KEY,
                payload_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                source TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                generation INTEGER,
                stage TEXT,
                symptom TEXT NOT NULL,
                action_key TEXT NOT NULL,
                executed INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                credit_eligible INTEGER NOT NULL,
                reward REAL,
                credit_reason TEXT NOT NULL,
                persisted_at TEXT NOT NULL
            )
            """
        )
        row = self._connection.execute(
            "SELECT value FROM ledger_meta WHERE key=?", ("schema_version",)
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO ledger_meta(key,value) VALUES(?,?)",
                ("schema_version", LEDGER_SCHEMA_VERSION),
            )
        elif row[0] != LEDGER_SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported executive ledger schema {row[0]!r}"
            )

    def append(self, record: ExecutiveEpisodeRecord) -> str:
        payload = _canonical_json(record.to_dict())
        digest = _sha256_text(payload)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._connection.execute(
                """
                SELECT payload_sha256
                FROM executive_episodes
                WHERE episode_id=?
                """,
                (record.episode_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != digest:
                    raise EpisodeLedgerConflict(
                        f"episode {record.episode_id} content conflict"
                    )
                self._connection.execute("COMMIT")
                return "already_present"

            episode = record.episode
            verification = record.verification
            credit = record.credit
            self._connection.execute(
                """
                INSERT INTO executive_episodes(
                    episode_id,
                    payload_sha256,
                    payload_json,
                    source,
                    source_sha256,
                    row_index,
                    run_id,
                    generation,
                    stage,
                    symptom,
                    action_key,
                    executed,
                    verdict,
                    credit_eligible,
                    reward,
                    credit_reason,
                    persisted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.episode_id,
                    digest,
                    payload,
                    episode.source,
                    episode.source_sha256,
                    episode.row_index,
                    episode.run_id,
                    episode.generation,
                    episode.stage,
                    episode.symptom,
                    episode.action_key,
                    1 if episode.executed else 0,
                    verification.outcome.verdict,
                    1 if credit.eligible else 0,
                    credit.reward,
                    credit.reason,
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._connection.execute("COMMIT")
            return "inserted"
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def quick_check(self) -> str:
        return str(self._connection.execute("PRAGMA quick_check").fetchone()[0])

    def count(self) -> int:
        return int(
            self._connection.execute(
                "SELECT COUNT(*) FROM executive_episodes"
            ).fetchone()[0]
        )

    def episode_digest(self, episode_id: str) -> str | None:
        row = self._connection.execute(
            """
            SELECT payload_sha256
            FROM executive_episodes
            WHERE episode_id=?
            """,
            (episode_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def episode_digests(
        self,
        episode_ids: tuple[str, ...],
    ) -> dict[str, str]:
        return {
            episode_id: digest
            for episode_id in episode_ids
            if (digest := self.episode_digest(episode_id)) is not None
        }

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
