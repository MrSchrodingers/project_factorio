from __future__ import annotations

import pytest

from factorio_ai_lab.evidence import EvidenceStatus, EvidenceValue


def test_observed_value_is_explicit_and_usable() -> None:
    value = EvidenceValue.observed(12.5, source="factorio.production")

    assert value.status is EvidenceStatus.OBSERVED
    assert value.require_observed() == 12.5
    assert value.to_dict()["confidence"] == 1.0


def test_missing_is_not_zero() -> None:
    value = EvidenceValue[float].missing(
        source="entity.power",
        reason="probe did not run",
    )

    assert value.value is None
    assert not value.usable
    with pytest.raises(ValueError):
        value.require_observed()


def test_invalid_cannot_carry_a_plausible_number() -> None:
    with pytest.raises(ValueError):
        EvidenceValue(
            value=0.0,
            status=EvidenceStatus.INVALID,
            source="broken probe",
        )


def test_estimate_requires_bounded_confidence() -> None:
    with pytest.raises(ValueError):
        EvidenceValue.estimated(
            2.0,
            source="world_model",
            reason="one-step rollout",
            confidence=1.1,
        )
