"""Decision layer that makes a world-model prediction usable, or refuses it.

Why this module exists
----------------------
A trained world model is not a decision input until three things are true at
the moment of the call: the prediction carries a declared uncertainty, the
feature being predicted has holdout evidence of beating the persistence
baseline, and the horizon asked for is one that was actually validated. Training
metrics alone cannot establish this, because training happens once per
generation while decisions happen continuously; a model that degraded, or a
feature that was never validated, would otherwise be consumed silently.

The refusal follows the precedent in ``model_arena.ModelArena.select``, which
declines to compare candidates across incommensurate protocols and states the
reason instead of returning a number that looks authoritative.

Why forecasting survival failure is a legitimate use of a weak model
--------------------------------------------------------------------
The use implemented here is anticipation, never substitution. Three properties
make it defensible even when the model is only modestly better than the
persistence baseline:

1. The measurement always travels with the forecast. ``Forecast.measured`` and
   ``SurvivalAlert.measured`` carry the observed value, so a consumer can never
   act on a prediction while being unaware of ground truth.
2. The trigger is the pessimistic end of the interval, not the point estimate.
   Crossing a survival threshold is decided by ``lower``, so model error has to
   overcome the whole confidence band before it can suppress an alarm.
3. The cost asymmetry favours early warning. A false alarm costs one cheap
   verification (inspect the fuel buffer); a missed depletion costs the run.
   This asymmetry only justifies a *warning*, and never a destructive action.

Where it stops being legitimate
-------------------------------
- When the model loses to persistence on the very feature being predicted. Then
  extrapolating the measured trend is both cheaper and more accurate, and the
  model adds error while borrowing the authority of "learned". This is enforced
  by ``WorldModelPolicy.verdict``.
- When the requested horizon exceeds the validated one. Holdout evidence here is
  one step ahead; multi-step roll-out feeds predictions back as inputs and its
  error compounds in a way no one measured. Enforced by ``FeatureEvidence.
  validated_horizon``.
- When the risk label is single-class over the evaluation window. An accuracy of
  1.0 against a constant label carries no information and the persistence
  baseline scores identically. Enforced by ``WorldModelPolicy.risk_verdict``.
- When the output is used to replace a measurement, or to authorize an
  irreversible action. Nothing in this module supports that, by construction:
  it emits verdicts and intervals, never commands.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from factorio_ai_lab.learning.model_arena import ModelArena
from factorio_ai_lab.learning.telemetry import FEATURE_NAMES, feature_vector

# Two-sided normal quantiles. Only tabulated levels are accepted: deriving an
# arbitrary quantile without a statistics dependency would mean inventing it.
Z_SCORES: dict[float, float] = {
    0.80: 1.2815515655446004,
    0.90: 1.6448536269514722,
    0.95: 1.959963984540054,
    0.99: 2.5758293035489004,
}

# Minimum number of independent generations behind a verdict. Matches the
# holdout protocol used by the world-model trainer so the two are commensurate.
MIN_FOLDS = 3

# Fraction of folds the model must win. Also mirrors the trainer's gate.
REQUIRED_WIN_RATE = 0.75

# Mean loss relative to persistence. A model that merely ties the baseline is
# not evidence of having learned the dynamics, so a margin is required.
MAX_LOSS_RATIO = 0.95

DEFAULT_SURVIVAL_FEATURE = "coal_reserve"


class WorldModelLike(Protocol):
    """Minimal surface a world model must expose to be driven by this layer."""

    def predict_next(
        self,
        sample: Mapping[str, Any],
        *,
        hidden: Any | None = ...,
    ) -> tuple[Mapping[str, float], float, Any]:
        ...


class NormalisedWorldModel(WorldModelLike, Protocol):
    """A world model that also exposes the per-feature normalisation scale."""

    scale: Any


@dataclass(frozen=True)
class FeatureEvidence:
    """Per-fold holdout errors for one feature, in normalized units.

    ``fold_model_mse`` and ``fold_persistence_mse`` must be aligned fold by
    fold, otherwise the win count is meaningless. ``scale`` converts normalized
    error back into the feature's own units so the interval can be reported
    where the consumer reasons.
    """

    feature: str
    fold_model_mse: tuple[float, ...]
    fold_persistence_mse: tuple[float, ...]
    scale: float
    validated_horizon: int = 1

    def __post_init__(self) -> None:
        if len(self.fold_model_mse) != len(self.fold_persistence_mse):
            raise ValueError("fold error series must be aligned fold by fold")
        if self.scale <= 0:
            raise ValueError("scale must be positive")
        if self.validated_horizon < 1:
            raise ValueError("validated_horizon must be >= 1")

    @property
    def fold_count(self) -> int:
        return len(self.fold_model_mse)

    @property
    def fold_wins(self) -> int:
        return sum(
            1
            for model, persistence in zip(
                self.fold_model_mse, self.fold_persistence_mse, strict=True
            )
            if model < persistence
        )

    @property
    def win_rate(self) -> float:
        return self.fold_wins / self.fold_count if self.fold_count else 0.0

    @property
    def mean_model_mse(self) -> float:
        return float(np.mean(self.fold_model_mse)) if self.fold_count else float("inf")

    @property
    def mean_persistence_mse(self) -> float:
        return float(np.mean(self.fold_persistence_mse)) if self.fold_count else 0.0

    @property
    def loss_ratio(self) -> float | None:
        baseline = self.mean_persistence_mse
        if baseline <= 0.0:
            return None
        return self.mean_model_mse / baseline

    @property
    def residual_sigma(self) -> float:
        """One-step residual standard deviation, in the feature's own units."""
        return math.sqrt(max(self.mean_model_mse, 0.0)) * self.scale


@dataclass(frozen=True)
class RiskEvidence:
    """Per-fold behaviour of the critical-fault channel on held-out generations."""

    fold_positive_rate: tuple[float, ...]
    fold_accuracy: tuple[float, ...]
    fold_persistence_accuracy: tuple[float, ...]

    def __post_init__(self) -> None:
        sizes = {
            len(self.fold_positive_rate),
            len(self.fold_accuracy),
            len(self.fold_persistence_accuracy),
        }
        if len(sizes) != 1:
            raise ValueError("risk fold series must be aligned fold by fold")

    @property
    def fold_count(self) -> int:
        return len(self.fold_positive_rate)

    @property
    def single_class_folds(self) -> int:
        return sum(1 for rate in self.fold_positive_rate if rate <= 0.0 or rate >= 1.0)

    @property
    def folds_beating_persistence(self) -> int:
        return sum(
            1
            for accuracy, persistence in zip(
                self.fold_accuracy, self.fold_persistence_accuracy, strict=True
            )
            if accuracy > persistence
        )


@dataclass(frozen=True)
class Verdict:
    """Eligibility decision for one prediction channel, with its reason."""

    subject: str
    eligible: bool
    reason: str
    fold_wins: int = 0
    fold_count: int = 0
    loss_ratio: float | None = None
    residual_sigma: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "eligible": self.eligible,
            "reason": self.reason,
            "fold_wins": self.fold_wins,
            "fold_count": self.fold_count,
            "loss_ratio": self.loss_ratio,
            "residual_sigma": self.residual_sigma,
        }


@dataclass(frozen=True)
class Forecast:
    """A prediction that states its own uncertainty, or declines to exist."""

    feature: str
    horizon: int
    usable: bool
    reason: str
    measured: float
    confidence: float
    verdict: Verdict
    value: float | None = None
    lower: float | None = None
    upper: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "horizon": self.horizon,
            "usable": self.usable,
            "reason": self.reason,
            "measured": self.measured,
            "confidence": self.confidence,
            "value": self.value,
            "lower": self.lower,
            "upper": self.upper,
            "verdict": self.verdict.to_dict(),
        }


@dataclass(frozen=True)
class SurvivalAlert:
    """Anticipation of a survival threshold being crossed inside a window.

    ``basis`` records what the conclusion rests on: ``measurement`` when the
    threshold is already breached in observed data, ``forecast`` when the
    pessimistic bound of an eligible prediction crosses it, and ``refused``
    when the model is not allowed to speak about this feature at all.
    """

    feature: str
    actionable: bool
    alarm: bool
    basis: str
    reason: str
    measured: float
    threshold: float
    horizon: int
    steps_to_threshold: int | None = None
    forecast: Forecast | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "actionable": self.actionable,
            "alarm": self.alarm,
            "basis": self.basis,
            "reason": self.reason,
            "measured": self.measured,
            "threshold": self.threshold,
            "horizon": self.horizon,
            "steps_to_threshold": self.steps_to_threshold,
            "forecast": self.forecast.to_dict() if self.forecast else None,
        }


class WorldModelPolicy:
    """Gate and interval layer between a world model and any decision."""

    def __init__(
        self,
        model: WorldModelLike,
        evidence: Mapping[str, FeatureEvidence],
        *,
        risk_evidence: RiskEvidence | None = None,
        arena: ModelArena | None = None,
        task: str = "world_model",
        confidence: float = 0.95,
        min_folds: int = MIN_FOLDS,
        required_win_rate: float = REQUIRED_WIN_RATE,
        max_loss_ratio: float = MAX_LOSS_RATIO,
    ) -> None:
        if confidence not in Z_SCORES:
            supported = ", ".join(str(level) for level in sorted(Z_SCORES))
            raise ValueError(f"confidence must be one of: {supported}")
        if not 0 < max_loss_ratio <= 1:
            raise ValueError("max_loss_ratio must be in (0, 1]")
        self.model = model
        self.evidence = dict(evidence)
        self.risk_evidence = risk_evidence
        self.arena = arena
        self.task = task
        self.confidence = confidence
        self.z_score = Z_SCORES[confidence]
        self.min_folds = min_folds
        self.required_win_rate = required_win_rate
        self.max_loss_ratio = max_loss_ratio

    def _arena_refusal(self) -> str | None:
        """Refusal reason coming from the model registry, if any."""
        if self.arena is None:
            return None
        record = self.arena.incumbent(self.task)
        if record is None:
            return (
                f"model arena has no promoted incumbent for task '{self.task}'; "
                "an unpromoted model does not decide"
            )
        if not bool(record.get("eligible", False)):
            return (
                f"model arena incumbent for task '{self.task}' is flagged ineligible"
            )
        return None

    def verdict(self, feature: str) -> Verdict:
        """Decide, at call time, whether this feature may inform a decision."""
        refusal = self._arena_refusal()
        if refusal is not None:
            return Verdict(subject=feature, eligible=False, reason=refusal)

        evidence = self.evidence.get(feature)
        if evidence is None:
            return Verdict(
                subject=feature,
                eligible=False,
                reason=f"no holdout evidence for feature '{feature}'",
            )

        common = {
            "subject": feature,
            "fold_wins": evidence.fold_wins,
            "fold_count": evidence.fold_count,
            "loss_ratio": evidence.loss_ratio,
            "residual_sigma": evidence.residual_sigma,
        }

        if evidence.fold_count < self.min_folds:
            return Verdict(
                eligible=False,
                reason=(
                    f"evidence spans {evidence.fold_count} folds; "
                    f"{self.min_folds} independent generations are required"
                ),
                **common,
            )

        if evidence.mean_persistence_mse <= 0.0:
            return Verdict(
                eligible=False,
                reason=(
                    "persistence baseline is exact on this feature; "
                    "the model can only add error"
                ),
                **common,
            )

        if evidence.win_rate < self.required_win_rate:
            return Verdict(
                eligible=False,
                reason=(
                    f"model beats persistence in only {evidence.fold_wins} of "
                    f"{evidence.fold_count} folds; "
                    f"win rate {evidence.win_rate:.2f} is below "
                    f"{self.required_win_rate:.2f}"
                ),
                **common,
            )

        ratio = evidence.loss_ratio
        if ratio is None or ratio > self.max_loss_ratio:
            shown = "undefined" if ratio is None else f"{ratio:.3f}"
            return Verdict(
                eligible=False,
                reason=(
                    f"insufficient margin over persistence: loss ratio {shown} "
                    f"exceeds {self.max_loss_ratio:.2f}"
                ),
                **common,
            )

        return Verdict(
            eligible=True,
            reason=(
                f"beats persistence in {evidence.fold_wins}/{evidence.fold_count} "
                f"folds with loss ratio {ratio:.3f}"
            ),
            **common,
        )

    def risk_verdict(self) -> Verdict:
        """Decide whether the critical-fault channel carries information."""
        refusal = self._arena_refusal()
        if refusal is not None:
            return Verdict(subject="critical_fault", eligible=False, reason=refusal)

        evidence = self.risk_evidence
        if evidence is None:
            return Verdict(
                subject="critical_fault",
                eligible=False,
                reason="no holdout evidence for the risk channel",
            )

        common = {
            "subject": "critical_fault",
            "fold_wins": evidence.folds_beating_persistence,
            "fold_count": evidence.fold_count,
        }

        if evidence.fold_count < self.min_folds:
            return Verdict(
                eligible=False,
                reason=(
                    f"risk evidence spans {evidence.fold_count} folds; "
                    f"{self.min_folds} independent generations are required"
                ),
                **common,
            )

        if evidence.single_class_folds:
            return Verdict(
                eligible=False,
                reason=(
                    f"{evidence.single_class_folds} of {evidence.fold_count} folds are "
                    "single-class; accuracy against a constant label is not evidence"
                ),
                **common,
            )

        if evidence.folds_beating_persistence < math.ceil(
            self.required_win_rate * evidence.fold_count
        ):
            return Verdict(
                eligible=False,
                reason=(
                    "risk head does not beat carrying the current fault flag "
                    "forward in enough folds"
                ),
                **common,
            )

        return Verdict(
            eligible=True,
            reason=(
                f"risk head beats the persistence flag in "
                f"{evidence.folds_beating_persistence}/{evidence.fold_count} folds"
            ),
            **common,
        )

    def _roll_out(
        self,
        sample: Mapping[str, Any],
        horizon: int,
        hidden: Any | None,
    ) -> list[Mapping[str, float]]:
        """Feed predictions back as inputs for ``horizon`` steps."""
        current: dict[str, Any] = dict(sample)
        states: list[Mapping[str, float]] = []
        for _ in range(horizon):
            prediction, _risk, hidden = self.model.predict_next(current, hidden=hidden)
            states.append(prediction)
            current = {**current, "features": dict(prediction)}
        return states

    def forecast(
        self,
        sample: Mapping[str, Any],
        feature: str,
        *,
        hidden: Any | None = None,
        horizon: int = 1,
    ) -> Forecast:
        """Predict ``feature`` ``horizon`` steps ahead with a declared interval."""
        if horizon < 1:
            raise ValueError("horizon must be >= 1")
        raw = sample.get("features", {})
        raw = raw if isinstance(raw, Mapping) else {}
        measured_raw = raw.get(feature, 0.0)
        measured = (
            float(measured_raw) if isinstance(measured_raw, (int, float)) else 0.0
        )

        verdict = self.verdict(feature)
        if not verdict.eligible:
            return Forecast(
                feature=feature,
                horizon=horizon,
                usable=False,
                reason=verdict.reason,
                measured=measured,
                confidence=self.confidence,
                verdict=verdict,
            )

        evidence = self.evidence[feature]
        if horizon > evidence.validated_horizon:
            return Forecast(
                feature=feature,
                horizon=horizon,
                usable=False,
                reason=(
                    f"requested horizon {horizon} exceeds the validated horizon "
                    f"{evidence.validated_horizon}; roll-out error is unmeasured"
                ),
                measured=measured,
                confidence=self.confidence,
                verdict=verdict,
            )

        states = self._roll_out(sample, horizon, hidden)
        value = float(states[-1].get(feature, measured))
        # Random-walk error accumulation. Deliberately conservative: it widens
        # the band with the horizon instead of pretending one-step residuals
        # hold over a roll-out that nobody measured beyond validated_horizon.
        half_width = self.z_score * evidence.residual_sigma * math.sqrt(horizon)
        return Forecast(
            feature=feature,
            horizon=horizon,
            usable=True,
            reason=verdict.reason,
            measured=measured,
            confidence=self.confidence,
            verdict=verdict,
            value=value,
            lower=value - half_width,
            upper=value + half_width,
        )

    def survival_alert(
        self,
        sample: Mapping[str, Any],
        *,
        feature: str = DEFAULT_SURVIVAL_FEATURE,
        threshold: float = 0.0,
        horizon: int = 1,
        hidden: Any | None = None,
    ) -> SurvivalAlert:
        """Warn when a survival buffer is projected to cross ``threshold``.

        Measurement wins over prediction: if the threshold is already breached
        in observed data, the alert says so and the model is not consulted.
        """
        if horizon < 1:
            raise ValueError("horizon must be >= 1")
        raw = sample.get("features", {})
        raw = raw if isinstance(raw, Mapping) else {}
        measured_raw = raw.get(feature, 0.0)
        measured = (
            float(measured_raw) if isinstance(measured_raw, (int, float)) else 0.0
        )

        if measured <= threshold:
            return SurvivalAlert(
                feature=feature,
                actionable=True,
                alarm=True,
                basis="measurement",
                reason="threshold already breached by measurement; no forecast needed",
                measured=measured,
                threshold=threshold,
                horizon=horizon,
                steps_to_threshold=0,
            )

        verdict = self.verdict(feature)
        if not verdict.eligible:
            return SurvivalAlert(
                feature=feature,
                actionable=False,
                alarm=False,
                basis="refused",
                reason=verdict.reason,
                measured=measured,
                threshold=threshold,
                horizon=horizon,
            )

        last: Forecast | None = None
        for step in range(1, horizon + 1):
            step_forecast = self.forecast(
                sample, feature, hidden=hidden, horizon=step
            )
            if not step_forecast.usable:
                return SurvivalAlert(
                    feature=feature,
                    actionable=False,
                    alarm=False,
                    basis="refused",
                    reason=step_forecast.reason,
                    measured=measured,
                    threshold=threshold,
                    horizon=horizon,
                    forecast=step_forecast,
                )
            last = step_forecast
            # The pessimistic bound decides: model error must overcome the whole
            # confidence band before it can suppress a warning.
            if step_forecast.lower is not None and step_forecast.lower <= threshold:
                return SurvivalAlert(
                    feature=feature,
                    actionable=True,
                    alarm=True,
                    basis="forecast",
                    reason=(
                        f"lower {self.confidence:.0%} bound crosses {threshold} "
                        f"within {step} step(s)"
                    ),
                    measured=measured,
                    threshold=threshold,
                    horizon=horizon,
                    steps_to_threshold=step,
                    forecast=step_forecast,
                )

        return SurvivalAlert(
            feature=feature,
            actionable=True,
            alarm=False,
            basis="forecast",
            reason=(
                f"lower {self.confidence:.0%} bound stays above {threshold} "
                f"for {horizon} step(s)"
            ),
            measured=measured,
            threshold=threshold,
            horizon=horizon,
            forecast=last,
        )


def holdout_feature_errors(
    model: NormalisedWorldModel,
    sequence: Sequence[Mapping[str, Any]],
) -> dict[str, tuple[float, float]]:
    """One-step normalized MSE per feature: ``(model, persistence)``.

    The sequence must come from a generation the model was not trained on;
    otherwise the numbers describe memorisation, not prediction.
    """
    ordered = sorted(sequence, key=lambda row: float(row.get("timestamp", 0.0)))
    if len(ordered) < 2:
        raise ValueError("a holdout sequence needs at least two samples")
    scale = np.asarray(model.scale, dtype=np.float64)

    hidden: Any | None = None
    model_errors: list[np.ndarray] = []
    persistence_errors: list[np.ndarray] = []
    for index in range(len(ordered) - 1):
        prediction, _risk, hidden = model.predict_next(ordered[index], hidden=hidden)
        predicted = np.asarray(
            [float(prediction[name]) for name in FEATURE_NAMES], dtype=np.float64
        )
        current = np.asarray(feature_vector(ordered[index]), dtype=np.float64)
        target = np.asarray(feature_vector(ordered[index + 1]), dtype=np.float64)
        model_errors.append(((predicted - target) / scale) ** 2)
        persistence_errors.append(((current - target) / scale) ** 2)

    model_mse = np.mean(np.vstack(model_errors), axis=0)
    persistence_mse = np.mean(np.vstack(persistence_errors), axis=0)
    return {
        name: (float(model_mse[index]), float(persistence_mse[index]))
        for index, name in enumerate(FEATURE_NAMES)
    }


def feature_evidence_from_folds(
    folds: Sequence[Mapping[str, tuple[float, float]]],
    scales: Mapping[str, float],
    *,
    validated_horizon: int = 1,
) -> dict[str, FeatureEvidence]:
    """Aggregate per-fold ``holdout_feature_errors`` output into evidence.

    Features without a positive scale are dropped: a degenerate scale makes the
    reported interval meaningless, and a meaningless interval must not be
    offered to a decision.
    """
    collected: dict[str, tuple[list[float], list[float]]] = {}
    for fold in folds:
        for name, pair in fold.items():
            model_mse, persistence_mse = pair
            bucket = collected.setdefault(name, ([], []))
            bucket[0].append(float(model_mse))
            bucket[1].append(float(persistence_mse))

    evidence: dict[str, FeatureEvidence] = {}
    for name, (model_series, persistence_series) in collected.items():
        scale = float(scales.get(name, 0.0))
        if scale <= 0.0:
            continue
        evidence[name] = FeatureEvidence(
            feature=name,
            fold_model_mse=tuple(model_series),
            fold_persistence_mse=tuple(persistence_series),
            scale=scale,
            validated_horizon=validated_horizon,
        )
    return evidence
