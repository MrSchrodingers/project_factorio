from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class EvidenceStatus(StrEnum):
    """Epistemic state of one value carried through the research system."""

    OBSERVED = "observed"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True)
class EvidenceValue(Generic[T]):
    """A value plus the evidence state that licenses its use.

    Missing and invalid observations deliberately carry no numeric payload.
    This prevents the historically expensive substitution "not measured -> 0".
    """

    value: T | None
    status: EvidenceStatus
    source: str
    reason: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.status in {EvidenceStatus.MISSING, EvidenceStatus.INVALID}:
            if self.value is not None:
                raise ValueError(f"{self.status.value} evidence cannot carry a value")
        elif self.value is None:
            raise ValueError(f"{self.status.value} evidence requires a value")

        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")

    @classmethod
    def observed(cls, value: T, *, source: str) -> EvidenceValue[T]:
        return cls(value=value, status=EvidenceStatus.OBSERVED, source=source, confidence=1.0)

    @classmethod
    def derived(cls, value: T, *, source: str, reason: str) -> EvidenceValue[T]:
        return cls(
            value=value,
            status=EvidenceStatus.DERIVED,
            source=source,
            reason=reason,
        )

    @classmethod
    def estimated(
        cls,
        value: T,
        *,
        source: str,
        reason: str,
        confidence: float,
    ) -> EvidenceValue[T]:
        return cls(
            value=value,
            status=EvidenceStatus.ESTIMATED,
            source=source,
            reason=reason,
            confidence=confidence,
        )

    @classmethod
    def missing(cls, *, source: str, reason: str) -> EvidenceValue[T]:
        return cls(
            value=None,
            status=EvidenceStatus.MISSING,
            source=source,
            reason=reason,
        )

    @classmethod
    def invalid(cls, *, source: str, reason: str) -> EvidenceValue[T]:
        return cls(
            value=None,
            status=EvidenceStatus.INVALID,
            source=source,
            reason=reason,
        )

    @property
    def usable(self) -> bool:
        return self.status not in {EvidenceStatus.MISSING, EvidenceStatus.INVALID}

    @property
    def is_observed(self) -> bool:
        return self.status is EvidenceStatus.OBSERVED

    def require_observed(self) -> T:
        if not self.is_observed or self.value is None:
            raise ValueError(
                f"observed evidence required; got {self.status.value} from {self.source}"
            )
        return self.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "status": self.status.value,
            "source": self.source,
            "reason": self.reason,
            "confidence": self.confidence,
        }
