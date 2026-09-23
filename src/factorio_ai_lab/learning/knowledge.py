from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)
_RATE_WORDS = re.compile(
    r"(?i)(?:per\s+second|/s\b|items?/s\b|per\s+minute|/min\b|items?/min\b)"
)


@dataclass(frozen=True)
class KnowledgeVerification:
    verified: bool
    unsupported_numbers: tuple[float, ...] = ()
    unknown_evidence_keys: tuple[str, ...] = ()
    rate_claim_without_rate_fact: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "unsupported_numbers": list(self.unsupported_numbers),
            "unknown_evidence_keys": list(self.unknown_evidence_keys),
            "rate_claim_without_rate_fact": self.rate_claim_without_rate_fact,
        }


def flatten_numeric_facts(
    facts: Mapping[str, Any],
    *,
    prefix: str = "",
) -> dict[str, float]:
    flattened: dict[str, float] = {}
    for key, value in facts.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            flattened[name] = float(value)
        elif isinstance(value, Mapping):
            flattened.update(flatten_numeric_facts(value, prefix=name))
    return flattened


def _number_supported(value: float, candidates: tuple[float, ...]) -> bool:
    for candidate in candidates:
        tolerance = max(1e-6, abs(candidate) * 0.015)
        if abs(value - candidate) <= tolerance:
            return True
        if abs(candidate) > 0 and abs(value - round(candidate, 1)) <= 1e-6:
            return True
        if abs(candidate) > 0 and abs(value - round(candidate, 2)) <= 1e-6:
            return True
    return False


def verify_generated_knowledge(
    *,
    lesson: str,
    hypothesis: str,
    facts: Mapping[str, Any],
    evidence_keys: list[str] | tuple[str, ...],
) -> KnowledgeVerification:
    numeric = flatten_numeric_facts(facts)
    candidates = tuple(numeric.values())
    unknown = tuple(sorted(key for key in evidence_keys if key not in facts))

    unsupported: list[float] = []
    for text in (lesson, hypothesis):
        for match in _NUMBER_RE.finditer(text):
            value = float(match.group(1))
            suffix = text[match.end() : match.end() + 1]
            if suffix == "%":
                value /= 100.0
            if not _number_supported(value, candidates):
                unsupported.append(float(match.group(1)))

    has_rate_fact = any(
        "rate" in key.lower()
        or key.lower().endswith("_per_s")
        or key.lower().endswith("_per_min")
        for key in numeric
    )
    rate_claim_without_rate_fact = bool(
        _RATE_WORDS.search(lesson + " " + hypothesis)
    ) and not has_rate_fact

    verified = not unsupported and not unknown and not rate_claim_without_rate_fact
    return KnowledgeVerification(
        verified=verified,
        unsupported_numbers=tuple(unsupported),
        unknown_evidence_keys=unknown,
        rate_claim_without_rate_fact=rate_claim_without_rate_fact,
    )


#: Why the recall answered what it answered. Written into the generation
#: report, because a stage-matched selection and a fallback one carry
#: different weight and must not be read as the same thing.
RECALL_BASIS_STAGE_MATCH = "stage_match"
RECALL_BASIS_RECENT_VERIFIED = "recent_verified"
RECALL_BASIS_NO_VERIFIED_LESSON = "no_verified_lesson"
RECALL_BASIS_MISSING_LOG = "missing_log"
RECALL_BASIS_EMPTY_LOG = "empty_log"
RECALL_BASIS_UNREADABLE_LOG = "unreadable_log"
RECALL_BASIS_NOT_CONSULTED = "not_consulted"

#: How many lessons are handed to one decision. The advisor context is held
#: under a character budget, so more lessons would be shorter lessons.
DEFAULT_RECALL_LIMIT = 3

#: Sorting key for a record whose timestamp cannot be parsed: it stays
#: eligible, ranked behind every record that does carry a readable one.
_OLDEST = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class RecalledLesson:
    """One verified lesson, identified by the instant it was written."""

    at: str
    stage: str
    lesson: str
    next_hypothesis: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "stage": self.stage,
            "lesson": self.lesson,
            "next_hypothesis": self.next_hypothesis,
            "source": self.source,
        }


@dataclass(frozen=True)
class KnowledgeRecall:
    """What the knowledge log answered, and how much of it was discarded.

    Both populations travel together on purpose: a reader who sees that the
    advice used knowledge has to be able to see how many lessons the gate
    threw away to produce it. The same applies to a log that is absent,
    empty or partially written, which is declared through `basis` instead of
    being flattened into an empty list that reads as "nothing was learned".
    """

    basis: str
    stage: str | None = None
    lessons: tuple[RecalledLesson, ...] = ()
    verified_count: int = 0
    unverified_count: int = 0
    stage_verified_count: int = 0
    malformed_lines: int = 0
    log_present: bool = False

    @property
    def lesson_ids(self) -> tuple[str, ...]:
        """The identifiers of the lessons that entered the decision."""
        return tuple(lesson.at for lesson in self.lessons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "stage": self.stage,
            "log_present": self.log_present,
            "verified_count": self.verified_count,
            "unverified_count": self.unverified_count,
            "stage_verified_count": self.stage_verified_count,
            "malformed_lines": self.malformed_lines,
            "lesson_ids": list(self.lesson_ids),
            "lessons": [lesson.to_dict() for lesson in self.lessons],
        }

    def to_advisor_context(self) -> dict[str, Any]:
        """The entries the advisor context carries, shaped to survive it.

        Two top-level entries rather than one nested block, and a lesson is
        one flat string rather than an object. Measured against the advisor's
        character budget: nested under one key, each lesson object was cut to
        two fields and `lesson` was the field dropped, so the advisor received
        identifiers and no text. A string is shortened by that budget, never
        discarded, and depth costs the text its share.

        The counts travel beside the lessons so advice cannot be read as
        using knowledge without showing how much the gate discarded.

        Lessons are emitted oldest first, the reverse of how they are stored
        and reported. That budget keeps the tail of a list when it has to
        drop rows, so oldest first is what makes the row it drops the oldest
        lesson instead of the newest one.
        """
        chronological = tuple(reversed(self.lessons))
        return {
            "verified_lessons": [
                f"{lesson.stage}: {lesson.lesson}" for lesson in chronological
            ],
            "knowledge_recall": {
                "basis": self.basis,
                "stage": self.stage,
                "verified": self.verified_count,
                "discarded": self.unverified_count,
                "lesson_ids": [lesson.at for lesson in chronological],
            },
        }


def recall_not_consulted() -> KnowledgeRecall:
    """The answer for a generation that never asked the knowledge log."""
    return KnowledgeRecall(basis=RECALL_BASIS_NOT_CONSULTED)


def _parse_instant(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _as_lesson(record: Mapping[str, Any]) -> RecalledLesson:
    return RecalledLesson(
        at=str(record.get("at", "")),
        stage=str(record.get("stage", "")),
        lesson=str(record.get("lesson", "")),
        next_hypothesis=str(record.get("next_hypothesis", "")),
        source=str(record.get("source", "")),
    )


def recall_verified_lessons(
    path: Path | str,
    *,
    stage: str | None = None,
    limit: int = DEFAULT_RECALL_LIMIT,
) -> KnowledgeRecall:
    """Read the knowledge log and answer the lessons a decision may use.

    Only a record whose `verification.verified` is exactly True is eligible:
    a record written before that field existed is unverified, not verified,
    so truthiness is not enough. Relevance is the stage of the current
    bottleneck, and recency breaks the tie inside it; when the bottleneck
    stage wrote no lesson, the most recent verified lessons are answered
    under an explicit fallback basis rather than an empty selection.

    A malformed line is counted and skipped, never fatal: the log is appended
    to while it is read, so its last line can be half written.
    """
    try:
        raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return KnowledgeRecall(basis=RECALL_BASIS_MISSING_LOG, stage=stage)
    except (OSError, UnicodeDecodeError):
        return KnowledgeRecall(basis=RECALL_BASIS_UNREADABLE_LOG, stage=stage)

    verified: list[tuple[datetime, int, RecalledLesson]] = []
    unverified_count = 0
    malformed_lines = 0
    records_seen = 0
    for index, raw in enumerate(raw_lines):
        text = raw.strip()
        if not text:
            continue
        records_seen += 1
        try:
            record = json.loads(text)
        except json.JSONDecodeError:
            malformed_lines += 1
            continue
        if not isinstance(record, dict):
            malformed_lines += 1
            continue
        verification = record.get("verification")
        if not (
            isinstance(verification, Mapping) and verification.get("verified") is True
        ):
            unverified_count += 1
            continue
        verified.append(
            (
                _parse_instant(record.get("at")) or _OLDEST,
                index,
                _as_lesson(record),
            )
        )

    matched = [row for row in verified if stage is not None and row[2].stage == stage]
    if matched:
        basis = RECALL_BASIS_STAGE_MATCH
        pool = matched
    elif verified:
        basis = RECALL_BASIS_RECENT_VERIFIED
        pool = verified
    elif records_seen:
        basis = RECALL_BASIS_NO_VERIFIED_LESSON
        pool = []
    else:
        basis = RECALL_BASIS_EMPTY_LOG
        pool = []

    newest_first = sorted(pool, key=lambda row: (row[0], row[1]), reverse=True)
    selected = tuple(row[2] for row in newest_first[: max(0, limit)])
    return KnowledgeRecall(
        basis=basis,
        stage=stage,
        lessons=selected,
        verified_count=len(verified),
        unverified_count=unverified_count,
        stage_verified_count=len(matched),
        malformed_lines=malformed_lines,
        log_present=True,
    )
