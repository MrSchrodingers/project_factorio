from __future__ import annotations


def rate_per_second(count: float, duration_s: float) -> float:
    """Normalize an observed count by its measurement window."""
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    return float(count) / float(duration_s)


def normalized_rate_ratio(
    *,
    candidate_count: float,
    candidate_duration_s: float,
    baseline_count: float,
    baseline_duration_s: float,
) -> float | None:
    """Compare rates measured over potentially different windows."""
    candidate_rate = rate_per_second(candidate_count, candidate_duration_s)
    baseline_rate = rate_per_second(baseline_count, baseline_duration_s)
    if baseline_rate <= 0:
        return None
    return candidate_rate / baseline_rate
