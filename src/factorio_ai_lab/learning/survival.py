from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FitnessVector:
    """Multi-objective evidence used for incumbent/challenger selection."""

    capabilities: frozenset[str] = field(default_factory=frozenset)
    rates_per_s: Mapping[str, float] = field(default_factory=dict)
    external_dependencies: int = 0
    failures: int = 0
    route_cost: float | None = None
    route_turns: int | None = None

    @property
    def total_rate_per_s(self) -> float:
        return sum(max(0.0, float(value)) for value in self.rates_per_s.values())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = sorted(self.capabilities)
        payload["rates_per_s"] = {
            key: round(float(value), 8)
            for key, value in sorted(self.rates_per_s.items())
        }
        payload["total_rate_per_s"] = round(self.total_rate_per_s, 8)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FitnessVector:
        rates_raw = payload.get("rates_per_s", {})
        rates = (
            {
                str(key): float(value)
                for key, value in rates_raw.items()
                if isinstance(key, str) and isinstance(value, (int, float))
            }
            if isinstance(rates_raw, Mapping)
            else {}
        )
        capabilities_raw = payload.get("capabilities", [])
        capabilities = frozenset(
            str(value)
            for value in capabilities_raw
            if isinstance(value, str)
        )
        route_cost_raw = payload.get("route_cost")
        route_turns_raw = payload.get("route_turns")
        return cls(
            capabilities=capabilities,
            rates_per_s=rates,
            external_dependencies=int(payload.get("external_dependencies", 0) or 0),
            failures=int(payload.get("failures", 0) or 0),
            route_cost=(
                float(route_cost_raw)
                if isinstance(route_cost_raw, (int, float))
                else None
            ),
            route_turns=(
                int(route_turns_raw)
                if isinstance(route_turns_raw, (int, float))
                else None
            ),
        )


@dataclass(frozen=True)
class PromotionDecision:
    promoted: bool
    reason: str
    regressions: tuple[str, ...]
    improvements: tuple[str, ...]
    retention_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "promoted": self.promoted,
            "reason": self.reason,
            "regressions": list(self.regressions),
            "improvements": list(self.improvements),
            "retention_ratio": self.retention_ratio,
        }


def compare_challenger(
    champion: FitnessVector | None,
    challenger: FitnessVector,
    *,
    retention_ratio: float = 0.80,
    throughput_improvement_ratio: float = 1.02,
) -> PromotionDecision:
    """
    Incumbent-preserving μ+λ-style selection for factory strategies.

    A challenger is first subject to hard survival constraints. It may not lose
    an established capability, regress an established production rate below the
    configured retention floor, add external dependencies, or add failures.
    Only survivors are eligible for promotion.
    """
    if not 0 < retention_ratio <= 1:
        raise ValueError("retention_ratio must be in (0, 1]")
    if throughput_improvement_ratio < 1:
        raise ValueError("throughput_improvement_ratio must be >= 1")

    if champion is None:
        regressions: list[str] = []
        if challenger.failures > 0:
            regressions.append(
                f"challenger has {challenger.failures} failed validation stage(s)"
            )
        if challenger.external_dependencies > 0:
            regressions.append(
                "challenger still depends on "
                f"{challenger.external_dependencies} external input(s)"
            )
        if regressions:
            return PromotionDecision(
                promoted=False,
                reason="no champion selected: challenger failed baseline survival gates",
                regressions=tuple(regressions),
                improvements=(),
                retention_ratio=retention_ratio,
            )
        return PromotionDecision(
            promoted=True,
            reason="first survival-qualified challenger becomes the initial champion",
            regressions=(),
            improvements=("initial_champion",),
            retention_ratio=retention_ratio,
        )

    regressions: list[str] = []
    improvements: list[str] = []

    lost_capabilities = sorted(champion.capabilities - challenger.capabilities)
    if lost_capabilities:
        regressions.append(
            "lost capabilities: " + ", ".join(lost_capabilities)
        )

    for name, baseline in champion.rates_per_s.items():
        baseline_value = max(0.0, float(baseline))
        if baseline_value <= 0:
            continue
        challenger_value = max(
            0.0,
            float(challenger.rates_per_s.get(name, 0.0)),
        )
        floor = baseline_value * retention_ratio
        if challenger_value + 1e-12 < floor:
            regressions.append(
                f"{name} rate {challenger_value:.4g}/s below "
                f"{retention_ratio:.0%} retention floor {floor:.4g}/s"
            )

    if challenger.external_dependencies > champion.external_dependencies:
        regressions.append(
            "external dependencies increased "
            f"{champion.external_dependencies}→{challenger.external_dependencies}"
        )
    elif challenger.external_dependencies < champion.external_dependencies:
        improvements.append(
            "external dependencies reduced "
            f"{champion.external_dependencies}→{challenger.external_dependencies}"
        )

    if challenger.failures > champion.failures:
        regressions.append(
            f"failed stages increased {champion.failures}→{challenger.failures}"
        )
    elif challenger.failures < champion.failures:
        improvements.append(
            f"failed stages reduced {champion.failures}→{challenger.failures}"
        )

    new_capabilities = sorted(challenger.capabilities - champion.capabilities)
    if new_capabilities:
        improvements.append("new capabilities: " + ", ".join(new_capabilities))

    champion_total = champion.total_rate_per_s
    challenger_total = challenger.total_rate_per_s
    if champion_total <= 0 < challenger_total:
        improvements.append("validated aggregate production throughput")
    elif (
        champion_total > 0
        and challenger_total
        >= champion_total * throughput_improvement_ratio
    ):
        improvements.append(
            f"aggregate rate improved {champion_total:.4g}→"
            f"{challenger_total:.4g}/s"
        )

    if (
        champion.route_cost is not None
        and challenger.route_cost is not None
        and challenger.route_cost + 1e-9 < champion.route_cost
    ):
        improvements.append(
            f"route cost improved {champion.route_cost:.4g}→"
            f"{challenger.route_cost:.4g}"
        )

    if (
        champion.route_turns is not None
        and challenger.route_turns is not None
        and challenger.route_turns < champion.route_turns
    ):
        improvements.append(
            f"route turns improved {champion.route_turns}→"
            f"{challenger.route_turns}"
        )

    if regressions:
        return PromotionDecision(
            promoted=False,
            reason="challenger rejected by survival constraints",
            regressions=tuple(regressions),
            improvements=tuple(improvements),
            retention_ratio=retention_ratio,
        )

    if improvements:
        return PromotionDecision(
            promoted=True,
            reason="challenger survives incumbent constraints and improves the frontier",
            regressions=(),
            improvements=tuple(improvements),
            retention_ratio=retention_ratio,
        )

    return PromotionDecision(
        promoted=False,
        reason="challenger survives but does not dominate the incumbent",
        regressions=(),
        improvements=(),
        retention_ratio=retention_ratio,
    )


def fitness_from_research(
    *,
    metrics: Mapping[str, Any],
    achieved: set[str] | frozenset[str],
    resource_accounting: Mapping[str, Any] | None = None,
    failed_stages: int = 0,
) -> FitnessVector:
    rates: dict[str, float] = {}

    metric_candidates = {
        "iron-system": ("survival_iron_rate_per_s",),
        "coal": ("survival_coal_rate_per_s", "coal_rate_per_s"),
        "copper-system": ("survival_copper_rate_per_s",),
        "iron-ore": ("scaled_iron_rate_per_s", "baseline_iron_rate_per_s"),
        "iron-plate": (
            "belt_smelting_plate_rate_per_s",
            "direct_smelting_plate_rate_per_s",
        ),
        "copper-ore": ("copper_ore_rate_per_s",),
        "copper-plate": ("copper_plate_rate_per_s",),
        "iron-gear-wheel": ("iron_gear_wheel_rate_per_s",),
        "automation-science-pack": ("automation_science_rate_per_s",),
        "logistic-science-pack": ("logistic_science_rate_per_s",),
    }
    for item, candidates in metric_candidates.items():
        for key in candidates:
            value = metrics.get(key)
            if isinstance(value, (int, float)) and float(value) > 0:
                rates[item] = float(value)
                break

    external_dependencies = 0
    if isinstance(resource_accounting, Mapping):
        raw = resource_accounting.get("exogenous_inputs", {})
        if isinstance(raw, Mapping):
            for value in raw.values():
                if not isinstance(value, Mapping):
                    continue
                status = str(value.get("status", "external"))
                if status not in {"retired", "internal", "self_sufficient"}:
                    external_dependencies += 1

    route_cost_raw = metrics.get("logistics_route_cost")
    route_turns_raw = metrics.get("logistics_turns")

    return FitnessVector(
        capabilities=frozenset(str(item) for item in achieved),
        rates_per_s=rates,
        external_dependencies=external_dependencies,
        failures=max(0, int(failed_stages)),
        route_cost=(
            float(route_cost_raw)
            if isinstance(route_cost_raw, (int, float))
            else None
        ),
        route_turns=(
            int(route_turns_raw)
            if isinstance(route_turns_raw, (int, float))
            else None
        ),
    )
