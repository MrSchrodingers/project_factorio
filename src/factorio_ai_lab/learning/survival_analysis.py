"""Survival analysis over factory generations.

The evolution loop records, per generation, how long the factory kept producing
(``productive_runtime_s``) and why it stopped (``halt_cause``). That pair is a
time-to-event observation, so the honest summary of it is a survival function,
not a mean runtime.

Why censoring matters
---------------------
A generation whose measurement window closed while the factory was still
producing (``halt_cause = none_observed``) did not die: we only know its
lifetime exceeds the observed time. That is a right-censored observation. If it
is counted as a death, every factor ``(n_j - d_j) / n_j`` below shrinks when it
should not, the estimated survival curve is pushed down, and the factory looks
more fragile than the evidence supports. The estimators here therefore keep a
censored observation in the risk set up to its time and remove it afterwards
without ever crediting it with an event.

What counts as an event
-----------------------
The event is the factory halting, that is, ``halt_cause`` inside
:data:`EVENT_HALT_CAUSES`. Promotion of a challenger is NOT used as an event
here, and should not be: promotion is an output of the selection rule, not of
the factory lifetime, and the rule currently rejects almost everything. Counted
in runs/evolution_history.jsonl on 2026-09-23, 35 of 37 records carry
``decision.promoted = false``, including rejections whose own payload lists new
capabilities and a lower route cost against a single failed stage. Fitting a
survival model to promotion would measure that rule and report it as factory
fragility.

Validity of the time axis
-------------------------
``productive_runtime_s`` is documented by its producer (learning/survival.py)
as a lower bound: the sum of the stage measurement windows that recorded
output. ``halt_cause`` is documented by its producer
(learning/factory_graph.classify_halt_cause) as a verdict about one terminal
snapshot, naming what was starved when the snapshot was taken and never when
the starvation began. The pair therefore has the shape of a time-to-event
observation without its full semantics: the time is quantised by the curriculum
design, which is why several generations report exactly the same runtime, and
the event is a state observed at the end of the window rather than a dated
failure.

Read ``S(t)`` as "the fraction of generations whose measured productive window
reached t without ending in a starved snapshot", not as the probability that a
factory is alive at t. Dating the failure would need a status time series or a
first-failure tick, which the current instrumentation does not emit; until it
does, every runtime here is closer to an administratively censored window than
to an observed lifetime, and the gate on every result exists to keep that
caveat attached to the numbers.

Estimators implemented
----------------------
Kaplan-Meier (product limit), over distinct event times ``t_1 < ... < t_k``
with ``d_j`` events at ``t_j`` and ``n_j`` observations still at risk just
before ``t_j``:

    S(t) = prod_{t_j <= t} (1 - d_j / n_j)

Greenwood variance of the estimate, used for the confidence interval:

    Var(S(t)) = S(t)^2 * sum_{t_j <= t} d_j / (n_j * (n_j - d_j))

The interval is computed on the log-log scale, ``theta = ln(-ln S)``, which
keeps both endpoints inside ``[0, 1]``:

    S(t) ^ exp(+/- z * se(theta))

Discrete hazard at an event time, the conditional probability of stopping at
``t_j`` given survival up to ``t_j``:

    h(t_j) = d_j / n_j

Nelson-Aalen cumulative hazard and its variance:

    H(t) = sum_{t_j <= t} d_j / n_j        Var(H(t)) = sum_{t_j <= t} d_j / n_j^2

Cumulative incidence (Aalen-Johansen) for cause ``c`` under competing risks,
where ``S`` is the all-cause Kaplan-Meier estimate and ``t_{j-1}`` is the event
time preceding ``t_j``:

    F_c(t) = sum_{t_j <= t} S(t_{j-1}) * d_{cj} / n_j

``sum_c F_c(t) = 1 - S(t)`` holds by construction. The complement of a
cause-specific Kaplan-Meier curve (other causes treated as censoring) does not
estimate ``F_c``: it overstates it, because it assumes a factory that died of
another cause could have been followed until it died of ``c``. That biased
quantity is available in :func:`cause_specific_kaplan_meier` for contrast only.

What is deliberately absent
---------------------------
No Cox model, no log-rank test, no confidence band for the cumulative
incidence. With the sample sizes this project currently has (4 instrumented
generations as of 2026-09-23), a regression coefficient or a p-value would be
noise with a decimal point on it. :func:`describe_by_stratum` returns the
descriptive summary plus an explicit refusal, and
:func:`required_events_for_hazard_ratio` answers how many events would be
needed instead.

Dependencies: standard library only. ``scipy`` is not installed in the test
environment, and the normal quantiles needed here come from
``statistics.NormalDist``. The estimators are written out explicitly because
the samples are small enough that clarity beats vectorisation.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any

#: Halt causes that are deaths: the factory stopped producing because an input
#: ran out.
EVENT_HALT_CAUSES: frozenset[str] = frozenset(
    {
        "fuel_starvation",
        "power_starvation",
        "fuel_and_power_starvation",
    }
)

#: The window closed with the factory still alive. Right-censored, not a death.
CENSORING_HALT_CAUSE = "none_observed"

#: No factory was ever built, so there is no lifetime to observe. Such a
#: generation contributes neither an event nor a censoring: it never entered
#: the risk set.
INELIGIBLE_HALT_CAUSE = "no_factory"

DISPOSITION_EVENT = "event"
DISPOSITION_CENSORED = "censored"
DISPOSITION_INELIGIBLE = "ineligible"
#: Halt cause absent (generation ran before the instrumentation) or unrecognised.
#: Excluded rather than assumed censored: assuming censoring would silently
#: push the curve up, which is the mirror image of the bias censoring exists to
#: avoid.
DISPOSITION_UNKNOWN = "unknown"

#: Bucket for events recorded without a cause under competing risks.
CAUSE_UNSPECIFIED = "unspecified"

VERDICT_EMPTY = "empty"
VERDICT_INSUFFICIENT = "insufficient"
VERDICT_SUFFICIENT = "sufficient"

#: Declared conventions of this module, not results derived from the data: the
#: minimum event counts below which the corresponding output is reported as
#: descriptive only. They are deliberately conservative because this project
#: has already been misled once by a metric that carried more confidence than
#: its base rate supported.
MIN_EVENTS_FOR_CURVE = 10
MIN_EVENTS_FOR_CAUSE_BREAKDOWN = 15
MIN_EVENTS_FOR_STRATUM_COMPARISON = 20

PURPOSE_CURVE = "survival_curve"
PURPOSE_HAZARD = "hazard"
PURPOSE_COMPETING_RISKS = "competing_risks"
PURPOSE_STRATUM_COMPARISON = "stratum_comparison"
PURPOSE_SAMPLE = "sample"

CovariateValue = float | str


@dataclass(frozen=True)
class Observation:
    """One factory lifetime.

    ``time_s`` is the productive runtime; ``event`` is True when the factory
    stopped (a death) and False when the observation is right-censored.
    """

    time_s: float
    event: bool
    cause: str | None = None
    label: str | None = None
    covariates: Mapping[str, CovariateValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.time_s, (int, float)) or isinstance(self.time_s, bool):
            raise TypeError(f"time_s must be a real number, got {self.time_s!r}")
        if not math.isfinite(self.time_s):
            raise ValueError(f"time_s must be finite, got {self.time_s!r}")
        if self.time_s < 0:
            raise ValueError(f"time_s must be non-negative, got {self.time_s!r}")


@dataclass(frozen=True)
class SampleGate:
    """Sample-size verdict attached to every result this module produces."""

    n: int
    n_events: int
    n_censored: int
    distinct_event_times: int
    purpose: str
    min_events_required: int
    sufficient: bool
    verdict: str
    reason: str


def _gate(observations: Sequence[Observation], purpose: str, min_events: int) -> SampleGate:
    n = len(observations)
    n_events = sum(1 for o in observations if o.event)
    n_censored = n - n_events
    distinct = len({o.time_s for o in observations if o.event})
    if n == 0:
        verdict = VERDICT_EMPTY
        reason = "no observations: nothing is estimated"
    elif n_events < min_events:
        verdict = VERDICT_INSUFFICIENT
        reason = (
            f"{n_events} event(s) and {n_censored} censored observation(s) in n={n}; "
            f"{purpose} requires at least {min_events} events under this module's "
            "declared convention. Read the output as description of these runs, "
            "not as an estimate that transfers to future generations."
        )
    else:
        verdict = VERDICT_SUFFICIENT
        reason = (
            f"{n_events} event(s) and {n_censored} censored observation(s) in n={n}; "
            f"meets the declared minimum of {min_events} events for {purpose}."
        )
    return SampleGate(
        n=n,
        n_events=n_events,
        n_censored=n_censored,
        distinct_event_times=distinct,
        purpose=purpose,
        min_events_required=min_events,
        sufficient=verdict == VERDICT_SUFFICIENT,
        verdict=verdict,
        reason=reason,
    )


def evaluate_sample_gate(
    observations: Iterable[Observation],
    *,
    purpose: str = PURPOSE_SAMPLE,
    min_events: int = MIN_EVENTS_FOR_CURVE,
) -> SampleGate:
    """Public entry point for the sample-size verdict of a bare sample."""

    return _gate(tuple(observations), purpose, min_events)


def classify_halt_cause(halt_cause: str | None) -> str:
    """Map a reported ``halt_cause`` onto its survival-analysis disposition."""

    if halt_cause in EVENT_HALT_CAUSES:
        return DISPOSITION_EVENT
    if halt_cause == CENSORING_HALT_CAUSE:
        return DISPOSITION_CENSORED
    if halt_cause == INELIGIBLE_HALT_CAUSE:
        return DISPOSITION_INELIGIBLE
    return DISPOSITION_UNKNOWN


@dataclass(frozen=True)
class ExtractionResult:
    """Observations pulled from generation reports, with what was dropped."""

    observations: tuple[Observation, ...]
    excluded: Mapping[str, int]
    gate: SampleGate


def _covariates(configuration: Mapping[str, Any]) -> dict[str, CovariateValue]:
    out: dict[str, CovariateValue] = {}
    for key, value in configuration.items():
        if isinstance(value, str):
            out[key] = value
        elif isinstance(value, (int, float)) and math.isfinite(value):
            # bool is a subclass of int and lands here as 0.0 / 1.0.
            out[key] = float(value)
    return out


def observations_from_generation_reports(
    reports: Iterable[Mapping[str, Any]],
) -> ExtractionResult:
    """Build observations from already-loaded generation report mappings.

    Pure: the caller does the file I/O. Generations without instrumentation are
    excluded and counted, never silently converted into censored observations.
    """

    observations: list[Observation] = []
    excluded = {DISPOSITION_UNKNOWN: 0, DISPOSITION_INELIGIBLE: 0, "no_runtime": 0}
    for index, report in enumerate(reports):
        challenger = report.get("challenger") or {}
        fitness = challenger.get("fitness") or {}
        disposition = classify_halt_cause(fitness.get("halt_cause"))
        if disposition in (DISPOSITION_UNKNOWN, DISPOSITION_INELIGIBLE):
            excluded[disposition] += 1
            continue
        runtime = fitness.get("productive_runtime_s")
        if not isinstance(runtime, (int, float)) or isinstance(runtime, bool):
            excluded["no_runtime"] += 1
            continue
        if not math.isfinite(runtime) or runtime <= 0:
            excluded["no_runtime"] += 1
            continue
        generation = report.get("generation")
        label = f"generation-{generation}" if generation is not None else str(index)
        observations.append(
            Observation(
                time_s=float(runtime),
                event=disposition == DISPOSITION_EVENT,
                cause=fitness.get("halt_cause") if disposition == DISPOSITION_EVENT else None,
                label=label,
                covariates=_covariates(challenger.get("configuration") or {}),
            )
        )
    return ExtractionResult(
        observations=tuple(observations),
        excluded=dict(excluded),
        gate=_gate(observations, PURPOSE_SAMPLE, MIN_EVENTS_FOR_CURVE),
    )


def _event_times(observations: Sequence[Observation]) -> list[float]:
    return sorted({o.time_s for o in observations if o.event})


def _at_risk(observations: Sequence[Observation], time_s: float) -> int:
    # An observation is at risk at t if its recorded time is >= t. A censoring
    # tied with an event therefore stays in the denominator of that event and
    # leaves the risk set only afterwards, which is the standard tie rule.
    return sum(1 for o in observations if o.time_s >= time_s)


@dataclass(frozen=True)
class SurvivalPoint:
    """One step of the product-limit estimate."""

    time_s: float
    n_at_risk: int
    n_events: int
    n_censored: int
    survival: float
    std_error: float | None
    ci_low: float | None
    ci_high: float | None
    conditional_hazard: float


@dataclass(frozen=True)
class KaplanMeierCurve:
    points: tuple[SurvivalPoint, ...]
    gate: SampleGate
    confidence_level: float
    follow_up_end_s: float | None

    def survival_at(self, time_s: float) -> float | None:
        """S(t), or None where the estimate is undefined.

        Undefined means: empty sample, negative time, or a time beyond the last
        observed follow-up, where the product limit carries no information.
        """

        if not self.points or self.follow_up_end_s is None:
            return None
        if time_s < 0 or time_s > self.follow_up_end_s:
            return None
        survival = 1.0
        for point in self.points:
            if point.time_s <= time_s:
                survival = point.survival
            else:
                break
        return survival

    @property
    def median_survival_s(self) -> float | None:
        """Smallest t with S(t) <= 0.5, or None when the curve never gets there."""

        for point in self.points:
            if point.survival <= 0.5:
                return point.time_s
        return None

    @property
    def median_survival_reason(self) -> str:
        if not self.points or len(self.points) == 1:
            return "no events observed: the median is not estimable"
        if self.median_survival_s is None:
            lowest = self.points[-1].survival
            return (
                f"survival never reaches 0.5 within follow-up (lowest S = {lowest:.4f}); "
                "the median is not estimable"
            )
        return "smallest event time with S(t) <= 0.5"


def _log_log_interval(
    survival: float, greenwood_sum: float, z_value: float
) -> tuple[float | None, float | None]:
    # Interval on theta = ln(-ln S). Undefined at S = 0 or S = 1, and whenever a
    # risk set was exhausted (n_j == d_j), which makes the Greenwood sum
    # infinite. Returning None there is preferable to printing a bound that the
    # data does not support.
    if not math.isfinite(greenwood_sum) or survival <= 0.0 or survival >= 1.0:
        return (None, None)
    log_survival = math.log(survival)
    se_theta = math.sqrt(greenwood_sum) / abs(log_survival)
    low = survival ** math.exp(z_value * se_theta)
    high = survival ** math.exp(-z_value * se_theta)
    return (min(max(low, 0.0), 1.0), min(max(high, 0.0), 1.0))


def kaplan_meier(
    observations: Iterable[Observation],
    *,
    confidence: float = 0.95,
    purpose: str = PURPOSE_CURVE,
    min_events: int = MIN_EVENTS_FOR_CURVE,
) -> KaplanMeierCurve:
    """Product-limit estimate of S(t) with right censoring."""

    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence!r}")
    sample = tuple(observations)
    gate = _gate(sample, purpose, min_events)
    if not sample:
        return KaplanMeierCurve(
            points=(), gate=gate, confidence_level=confidence, follow_up_end_s=None
        )

    z_value = NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)
    points: list[SurvivalPoint] = [
        SurvivalPoint(
            time_s=0.0,
            n_at_risk=len(sample),
            n_events=0,
            n_censored=0,
            survival=1.0,
            std_error=0.0,
            ci_low=None,
            ci_high=None,
            conditional_hazard=0.0,
        )
    ]
    survival = 1.0
    greenwood = 0.0
    previous_time = 0.0
    for time_s in _event_times(sample):
        n_at_risk = _at_risk(sample, time_s)
        n_events = sum(1 for o in sample if o.event and o.time_s == time_s)
        n_censored = sum(
            1 for o in sample if not o.event and previous_time < o.time_s <= time_s
        )
        hazard = n_events / n_at_risk
        survival *= 1.0 - hazard
        if n_at_risk > n_events:
            greenwood += n_events / (n_at_risk * (n_at_risk - n_events))
        else:
            greenwood = math.inf
        std_error = survival * math.sqrt(greenwood) if math.isfinite(greenwood) else None
        ci_low, ci_high = _log_log_interval(survival, greenwood, z_value)
        points.append(
            SurvivalPoint(
                time_s=time_s,
                n_at_risk=n_at_risk,
                n_events=n_events,
                n_censored=n_censored,
                survival=survival,
                std_error=std_error,
                ci_low=ci_low,
                ci_high=ci_high,
                conditional_hazard=hazard,
            )
        )
        previous_time = time_s
    return KaplanMeierCurve(
        points=tuple(points),
        gate=gate,
        confidence_level=confidence,
        follow_up_end_s=max(o.time_s for o in sample),
    )


@dataclass(frozen=True)
class HazardPoint:
    time_s: float
    n_at_risk: int
    n_events: int
    conditional_hazard: float
    hazard_rate_per_s: float | None
    cumulative_hazard: float
    cumulative_hazard_se: float


@dataclass(frozen=True)
class HazardTable:
    points: tuple[HazardPoint, ...]
    gate: SampleGate


def hazard_table(
    observations: Iterable[Observation],
    *,
    min_events: int = MIN_EVENTS_FOR_CURVE,
) -> HazardTable:
    """Discrete hazard and Nelson-Aalen cumulative hazard.

    ``conditional_hazard`` is ``d_j / n_j``, a probability, not a rate.
    ``hazard_rate_per_s`` divides it by the width of the interval that starts at
    the event time and ends at the next one, so it is only defined while there
    is a next event time and it depends on the observed time grid. This is the
    quantity a mean runtime destroys: two samples can share a mean and differ in
    whether the risk is front-loaded or back-loaded.
    """

    sample = tuple(observations)
    gate = _gate(sample, PURPOSE_HAZARD, min_events)
    times = _event_times(sample)
    points: list[HazardPoint] = []
    cumulative = 0.0
    variance = 0.0
    for index, time_s in enumerate(times):
        n_at_risk = _at_risk(sample, time_s)
        n_events = sum(1 for o in sample if o.event and o.time_s == time_s)
        hazard = n_events / n_at_risk
        cumulative += hazard
        variance += n_events / (n_at_risk**2)
        width = times[index + 1] - time_s if index + 1 < len(times) else None
        rate = hazard / width if width else None
        points.append(
            HazardPoint(
                time_s=time_s,
                n_at_risk=n_at_risk,
                n_events=n_events,
                conditional_hazard=hazard,
                hazard_rate_per_s=rate,
                cumulative_hazard=cumulative,
                cumulative_hazard_se=math.sqrt(variance),
            )
        )
    return HazardTable(points=tuple(points), gate=gate)


def cause_specific_kaplan_meier(
    observations: Iterable[Observation],
    cause: str,
    *,
    confidence: float = 0.95,
) -> KaplanMeierCurve:
    """Kaplan-Meier for one cause with the competing causes treated as censoring.

    Provided for contrast: ``1 - S`` from this curve is NOT the probability of
    dying from ``cause``. It overstates it, because censoring a competing death
    asserts that the factory remained eligible to die of ``cause`` afterwards,
    which it did not. Use :func:`competing_risks` for the incidence.
    """

    recoded = [
        Observation(
            time_s=o.time_s,
            event=o.event and (o.cause or CAUSE_UNSPECIFIED) == cause,
            cause=cause if o.event and (o.cause or CAUSE_UNSPECIFIED) == cause else None,
            label=o.label,
            covariates=o.covariates,
        )
        for o in observations
    ]
    return kaplan_meier(recoded, confidence=confidence)


@dataclass(frozen=True)
class IncidencePoint:
    time_s: float
    n_at_risk: int
    n_events_cause: int
    incidence: float


@dataclass(frozen=True)
class CompetingRisksResult:
    """Aalen-Johansen cumulative incidence per cause.

    No confidence band is reported: the variance of the cumulative incidence is
    not implemented, because at the sample sizes seen here the band would be
    wider than the unit interval it lives in and would only lend false
    precision. The gate carries the counts that justify that refusal.
    """

    incidence: Mapping[str, tuple[IncidencePoint, ...]]
    overall: KaplanMeierCurve
    cause_counts: Mapping[str, int]
    gate: SampleGate

    def incidence_at(self, cause: str, time_s: float) -> float | None:
        points = self.incidence.get(cause)
        if points is None or self.overall.follow_up_end_s is None:
            return None
        if time_s < 0 or time_s > self.overall.follow_up_end_s:
            return None
        value = 0.0
        for point in points:
            if point.time_s <= time_s:
                value = point.incidence
            else:
                break
        return value

    def total_incidence_at(self, time_s: float) -> float | None:
        total = 0.0
        for cause in self.incidence:
            value = self.incidence_at(cause, time_s)
            if value is None:
                return None
            total += value
        return total


def competing_risks(
    observations: Iterable[Observation],
    *,
    confidence: float = 0.95,
    min_events: int = MIN_EVENTS_FOR_CAUSE_BREAKDOWN,
) -> CompetingRisksResult:
    """Cumulative incidence per cause under competing risks.

    ``F_c(t) = sum_{t_j <= t} S(t_{j-1}) * d_{cj} / n_j`` with ``S`` the
    all-cause Kaplan-Meier estimate. The causes are not assumed independent:
    each death removes the factory from the risk set for every other cause,
    which is exactly why ``1 - S_c(t)`` is the wrong answer to "what is the
    probability of dying of fuel starvation by t".
    """

    sample = tuple(observations)
    gate = _gate(sample, PURPOSE_COMPETING_RISKS, min_events)
    overall = kaplan_meier(
        sample,
        confidence=confidence,
        purpose=PURPOSE_COMPETING_RISKS,
        min_events=min_events,
    )
    causes: dict[str, int] = {}
    for observation in sample:
        if observation.event:
            key = observation.cause or CAUSE_UNSPECIFIED
            causes[key] = causes.get(key, 0) + 1
    incidence: dict[str, list[IncidencePoint]] = {cause: [] for cause in causes}
    running = {cause: 0.0 for cause in causes}
    survival_before = 1.0
    for time_s in _event_times(sample):
        n_at_risk = _at_risk(sample, time_s)
        for cause in causes:
            d_cause = sum(
                1
                for o in sample
                if o.event and o.time_s == time_s and (o.cause or CAUSE_UNSPECIFIED) == cause
            )
            running[cause] += survival_before * d_cause / n_at_risk
            incidence[cause].append(
                IncidencePoint(
                    time_s=time_s,
                    n_at_risk=n_at_risk,
                    n_events_cause=d_cause,
                    incidence=running[cause],
                )
            )
        d_total = sum(1 for o in sample if o.event and o.time_s == time_s)
        survival_before *= 1.0 - d_total / n_at_risk
    return CompetingRisksResult(
        incidence={cause: tuple(points) for cause, points in incidence.items()},
        overall=overall,
        cause_counts=dict(causes),
        gate=gate,
    )


@dataclass(frozen=True)
class MedianInterval:
    """Distribution-free interval for the median built from order statistics.

    For uncensored times ``X_(1) <= ... <= X_(n)``, the interval
    ``[X_(k), X_(n+1-k)]`` covers the population median with probability
    ``1 - 2 * P(Bin(n, 1/2) <= k - 1)``, computed exactly here. The achieved
    coverage is reported because at small ``n`` the nominal level is simply not
    reachable: with ``n = 4`` the widest possible interval covers 0.875, and
    saying "95%" would be a claim the sample cannot back.
    """

    low: float
    high: float
    nominal_level: float
    achieved_coverage: float
    meets_nominal_level: bool
    order_statistic_k: int
    method: str = "exact_binomial_order_statistics"


def _median_interval(times: Sequence[float], nominal: float) -> MedianInterval | None:
    n = len(times)
    if n < 2:
        return None
    ordered = sorted(times)
    chosen_k = 1
    chosen_coverage = 1.0 - 2.0 * sum(math.comb(n, i) for i in range(1)) / 2.0**n
    for k in range(1, n // 2 + 1):
        coverage = 1.0 - 2.0 * sum(math.comb(n, i) for i in range(k)) / 2.0**n
        if coverage >= nominal:
            chosen_k, chosen_coverage = k, coverage
        else:
            break
    return MedianInterval(
        low=ordered[chosen_k - 1],
        high=ordered[n - chosen_k],
        nominal_level=nominal,
        achieved_coverage=chosen_coverage,
        meets_nominal_level=chosen_coverage >= nominal,
        order_statistic_k=chosen_k,
    )


@dataclass(frozen=True)
class StratumSummary:
    name: str
    n: int
    n_events: int
    n_censored: int
    min_time_s: float | None
    max_time_s: float | None
    observed_median_s: float | None
    km_median_s: float | None
    km_median_reason: str
    median_ci: MedianInterval | None
    median_ci_refusal: str
    gate: SampleGate


@dataclass(frozen=True)
class RequiredSample:
    hazard_ratio: float
    alpha: float
    power: float
    allocation: float
    event_probability: float | None
    required_events: int
    required_observations: int | None
    method: str
    note: str


@dataclass(frozen=True)
class StratumComparison:
    """Descriptive summary per stratum plus an explicit refusal to infer."""

    covariate: str | None
    threshold: float | None
    strata: tuple[StratumSummary, ...]
    excluded_missing_covariate: int
    inference: None
    inference_refusal: str
    required_for_inference: RequiredSample | None
    gate: SampleGate


def required_events_for_hazard_ratio(
    hazard_ratio: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
    allocation: float = 0.5,
    event_probability: float | None = None,
) -> RequiredSample:
    """How many events a two-group comparison would need to detect ``hazard_ratio``.

    Under proportional hazards the estimated log hazard ratio has asymptotic
    variance ``1 / (d * p * (1 - p))`` with ``d`` events and allocation ``p``.
    Requiring a two-sided test at level ``alpha`` to reject with probability
    ``power`` gives

        d = (z_{1 - alpha/2} + z_{power})^2 / (p * (1 - p) * ln(HR)^2)

    and, given the probability that an observation produces an event,
    ``n = ceil(d / P(event))``. A null effect (``HR = 1``) needs infinitely many
    events and is refused rather than approximated.
    """

    if not isinstance(hazard_ratio, (int, float)) or isinstance(hazard_ratio, bool):
        raise TypeError(f"hazard_ratio must be a real number, got {hazard_ratio!r}")
    if not math.isfinite(hazard_ratio) or hazard_ratio <= 0.0:
        raise ValueError(f"hazard_ratio must be positive and finite, got {hazard_ratio!r}")
    if hazard_ratio == 1.0:
        raise ValueError(
            "hazard_ratio = 1 is the null effect: no finite number of events detects it"
        )
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")
    if not 0.0 < power < 1.0:
        raise ValueError(f"power must be in (0, 1), got {power!r}")
    if not 0.0 < allocation < 1.0:
        raise ValueError(f"allocation must be in (0, 1), got {allocation!r}")
    if event_probability is not None and not 0.0 < event_probability <= 1.0:
        raise ValueError(f"event_probability must be in (0, 1], got {event_probability!r}")

    normal = NormalDist()
    z_alpha = normal.inv_cdf(1.0 - alpha / 2.0)
    z_beta = normal.inv_cdf(power)
    log_ratio = math.log(hazard_ratio)
    events = (z_alpha + z_beta) ** 2 / (allocation * (1.0 - allocation) * log_ratio**2)
    required_events = math.ceil(events)
    required_observations = (
        math.ceil(required_events / event_probability) if event_probability else None
    )
    return RequiredSample(
        hazard_ratio=float(hazard_ratio),
        alpha=alpha,
        power=power,
        allocation=allocation,
        event_probability=event_probability,
        required_events=required_events,
        required_observations=required_observations,
        method="proportional_hazards_asymptotic_normal",
        note=(
            "Asymptotic result: it describes how many events would be needed, not "
            "what the current sample supports."
        ),
    )


def _stratum_summary(
    name: str, observations: Sequence[Observation], confidence: float
) -> StratumSummary:
    gate = _gate(observations, PURPOSE_STRATUM_COMPARISON, MIN_EVENTS_FOR_STRATUM_COMPARISON)
    times = [o.time_s for o in observations]
    censored = [o for o in observations if not o.event]
    curve = kaplan_meier(
        observations,
        confidence=confidence,
        purpose=PURPOSE_STRATUM_COMPARISON,
        min_events=MIN_EVENTS_FOR_STRATUM_COMPARISON,
    )
    interval: MedianInterval | None = None
    refusal = ""
    if censored:
        # Order statistics of observed times assume every time is a realised
        # lifetime. With censoring present the sorted times are not a sample of
        # the lifetime distribution, so the interval is refused and the
        # Kaplan-Meier median is the quantity to read instead.
        refusal = (
            f"{len(censored)} censored observation(s) in this stratum: the exact "
            "order-statistic interval assumes uncensored times; read km_median_s instead"
        )
    elif len(times) < 2:
        refusal = "fewer than 2 observations: no interval is defined"
    else:
        interval = _median_interval(times, confidence)
        if interval is None:
            refusal = "no interval is defined for this sample size"
    observed_median = None
    if times:
        ordered = sorted(times)
        middle = len(ordered) // 2
        observed_median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2.0
        )
    return StratumSummary(
        name=name,
        n=len(observations),
        n_events=gate.n_events,
        n_censored=gate.n_censored,
        min_time_s=min(times) if times else None,
        max_time_s=max(times) if times else None,
        observed_median_s=observed_median,
        km_median_s=curve.median_survival_s,
        km_median_reason=curve.median_survival_reason,
        median_ci=interval,
        median_ci_refusal=refusal,
        gate=gate,
    )


def describe_by_stratum(
    observations: Iterable[Observation],
    covariate: str | None = None,
    *,
    threshold: float | None = None,
    confidence: float = 0.95,
    reference_hazard_ratio: float = 2.0,
) -> StratumComparison:
    """Describe survival per stratum of one covariate, and refuse to infer.

    Numeric covariates are split at ``threshold`` (the observed median when
    None); non-numeric ones are grouped by value. Observations missing the
    covariate are excluded and counted, never imputed.

    ``inference`` is always None. No log-rank test, hazard ratio or p-value is
    produced: with the event counts this project has, such a number would be
    indistinguishable from noise, and ``required_for_inference`` states how many
    events a comparison at ``reference_hazard_ratio`` would need instead.
    """

    sample = tuple(observations)
    gate = _gate(sample, PURPOSE_STRATUM_COMPARISON, MIN_EVENTS_FOR_STRATUM_COMPARISON)
    if covariate is None:
        strata = (_stratum_summary("all", sample, confidence),) if sample else ()
        return StratumComparison(
            covariate=None,
            threshold=None,
            strata=strata,
            excluded_missing_covariate=0,
            inference=None,
            inference_refusal="single stratum: there is nothing to compare",
            required_for_inference=None,
            gate=gate,
        )

    present = [o for o in sample if covariate in o.covariates]
    excluded = len(sample) - len(present)
    values = [o.covariates[covariate] for o in present]
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)

    groups: dict[str, list[Observation]] = {}
    used_threshold: float | None = None
    if present and numeric:
        numbers = sorted(float(v) for v in values)
        if threshold is None:
            middle = len(numbers) // 2
            used_threshold = (
                numbers[middle]
                if len(numbers) % 2
                else (numbers[middle - 1] + numbers[middle]) / 2.0
            )
        else:
            used_threshold = float(threshold)
        low_name = f"{covariate} <= {used_threshold:g}"
        high_name = f"{covariate} > {used_threshold:g}"
        for observation in present:
            value = float(observation.covariates[covariate])  # type: ignore[arg-type]
            name = low_name if value <= used_threshold else high_name
            groups.setdefault(name, []).append(observation)
    else:
        for observation in present:
            name = f"{covariate}={observation.covariates[covariate]}"
            groups.setdefault(name, []).append(observation)

    strata = tuple(
        _stratum_summary(name, members, confidence)
        for name, members in sorted(groups.items())
        if members
    )
    if len(strata) < 2:
        refusal = "fewer than 2 non-empty strata: there is nothing to compare"
        required = None
    else:
        required = required_events_for_hazard_ratio(reference_hazard_ratio)
        refusal = (
            f"no inferential test is reported: {gate.n_events} event(s) observed, while "
            f"detecting a hazard ratio of {reference_hazard_ratio:g} at alpha={required.alpha:g} "
            f"with power {required.power:g} needs about {required.required_events} events. "
            "The stratum summaries are descriptions of these runs only."
        )
    return StratumComparison(
        covariate=covariate,
        threshold=used_threshold,
        strata=strata,
        excluded_missing_covariate=excluded,
        inference=None,
        inference_refusal=refusal,
        required_for_inference=required,
        gate=gate,
    )
