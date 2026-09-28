"""Exact preregistered inference for F4-C paired delta_J."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

INFERENCE_VERSION = "cortex_f4c_exact_sign_flip_v1"


@dataclass(frozen=True)
class ExactConfidenceSet:
    alpha: float
    lower: float
    upper: float
    connected: bool
    component_count: int
    components: tuple[tuple[float, float], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "lower": self.lower,
            "upper": self.upper,
            "connected": self.connected,
            "component_count": self.component_count,
            "components": [list(row) for row in self.components],
        }


def _finite_deltas(values: Iterable[float]) -> tuple[float, ...]:
    rows = tuple(float(value) for value in values)
    if not rows:
        raise ValueError("at least one paired delta is required")
    if any(not math.isfinite(value) for value in rows):
        raise ValueError("all paired deltas must be finite")
    return rows


def _sign_sums(values: Sequence[float]) -> tuple[np.ndarray, np.ndarray]:
    sums = np.array([0.0], dtype=np.float64)
    signs = np.array([0], dtype=np.int16)
    for value in values:
        sums = np.concatenate((sums + value, sums - value))
        signs = np.concatenate((signs + 1, signs - 1))
    return sums, signs


def exact_one_sided_sign_flip_p(
    values: Iterable[float],
    *,
    alternative: str = "greater",
) -> float:
    deltas = _finite_deltas(values)
    sums, _ = _sign_sums(deltas)
    observed = float(sum(deltas))
    tolerance = 1e-12
    if alternative == "greater":
        count = int(np.count_nonzero(sums >= observed - tolerance))
    elif alternative == "less":
        count = int(np.count_nonzero(sums <= observed + tolerance))
    else:
        raise ValueError("alternative must be greater or less")
    return count / len(sums)


def exact_two_sided_shift_p(
    values: Iterable[float],
    tau: float,
) -> float:
    deltas = _finite_deltas(values)
    sums, sign_counts = _sign_sums(deltas)
    n = len(deltas)
    observed = float(sum(deltas)) - n * float(tau)
    permuted = sums - float(tau) * sign_counts
    count = int(
        np.count_nonzero(
            np.abs(permuted) >= abs(observed) - 1e-12
        )
    )
    return count / len(sums)


def _coverage_components(
    starts: np.ndarray,
    ends: np.ndarray,
    *,
    base_count: int,
    threshold: int,
) -> tuple[tuple[float, float], ...]:
    if base_count >= threshold:
        return ((-math.inf, math.inf),)

    starts = np.sort(starts)
    ends = np.sort(ends)
    i = 0
    j = 0
    active = base_count
    current: float | None = None
    components: list[tuple[float, float]] = []
    while i < len(starts) or j < len(ends):
        next_start = starts[i] if i < len(starts) else math.inf
        next_end = ends[j] if j < len(ends) else math.inf
        x = float(min(next_start, next_end))

        i2 = int(np.searchsorted(starts, x, side="right"))
        j2 = int(np.searchsorted(ends, x, side="right"))
        n_start = i2 - i
        n_end = j2 - j

        count_at = active + n_start
        after = active + n_start - n_end
        point_accept = count_at >= threshold
        after_accept = after >= threshold

        if current is None and point_accept:
            current = x
        if current is not None and point_accept and not after_accept:
            components.append((current, x))
            current = None

        active = after
        i = i2
        j = j2

    if current is not None:
        components.append((current, math.inf))
    return tuple(components)


def exact_shift_confidence_set(
    values: Iterable[float],
    *,
    alpha: float = 0.05,
) -> ExactConfidenceSet:
    """Invert the two-sided exact sign-flip test for constant shift tau."""

    deltas = _finite_deltas(values)
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must lie in (0,1)")
    n = len(deltas)
    sums, sign_counts = _sign_sums(deltas)
    total = len(sums)
    threshold = math.floor(alpha * total + 1e-12) + 1

    extreme = np.abs(sign_counts) == n
    base_count = int(np.count_nonzero(extreme))
    finite = ~extreme
    a = sums[finite]
    b = sign_counts[finite].astype(np.float64)
    total_delta = float(sum(deltas))
    r1 = (total_delta - a) / (n - b)
    r2 = (a + total_delta) / (n + b)
    starts = np.minimum(r1, r2)
    ends = np.maximum(r1, r2)
    components = _coverage_components(
        starts,
        ends,
        base_count=base_count,
        threshold=threshold,
    )
    if not components:
        raise RuntimeError("exact confidence set is empty")
    return ExactConfidenceSet(
        alpha=alpha,
        lower=float(components[0][0]),
        upper=float(components[-1][1]),
        connected=len(components) == 1,
        component_count=len(components),
        components=components,
    )


def paired_cohens_dz(values: Iterable[float]) -> float | None:
    deltas = _finite_deltas(values)
    if len(deltas) < 2:
        return None
    sd = statistics.stdev(deltas)
    if sd <= 0.0:
        return None
    return statistics.fmean(deltas) / sd


def summarize_primary_inference(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_families: Sequence[str],
    alpha: float = 0.05,
    sesoi: float = 0.05,
    minimum_valid_pairs: int = 16,
    minimum_per_family: int = 3,
) -> dict[str, Any]:
    valid = [
        row
        for row in rows
        if row.get("valid_for_primary_inference") is True
    ]
    deltas = _finite_deltas(float(row["delta_J"]) for row in valid)
    family_counts = {
        family: sum(1 for row in valid if row.get("family") == family)
        for family in expected_families
    }
    sample_sufficient = (
        len(valid) >= minimum_valid_pairs
        and all(
            family_counts[family] >= minimum_per_family
            for family in expected_families
        )
    )
    mean_delta = statistics.fmean(deltas)
    median_delta = statistics.median(deltas)
    dz = paired_cohens_dz(deltas)
    p_one_sided = exact_one_sided_sign_flip_p(
        deltas,
        alternative="greater",
    )
    confidence = exact_shift_confidence_set(deltas, alpha=alpha)
    positive = (
        sample_sufficient
        and p_one_sided <= alpha
        and confidence.lower > 0.0
        and mean_delta >= sesoi
    )
    return {
        "inference_version": INFERENCE_VERSION,
        "valid_pair_count": len(valid),
        "family_valid_counts": family_counts,
        "sample_sufficient": sample_sufficient,
        "mean_delta_J": mean_delta,
        "median_delta_J": median_delta,
        "paired_cohens_dz": dz,
        "one_sided_exact_p": p_one_sided,
        "confidence_set_95pct": confidence.to_dict(),
        "alpha": alpha,
        "sesoi_delta_J": sesoi,
        "minimum_valid_pairs": minimum_valid_pairs,
        "minimum_per_family": minimum_per_family,
        "positive_causal_memory_result": positive,
        "decision": (
            "positive"
            if positive
            else ("not_positive" if sample_sufficient else "inconclusive")
        ),
    }
