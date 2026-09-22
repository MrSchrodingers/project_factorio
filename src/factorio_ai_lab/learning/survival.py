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
    autonomy_score: float | None = None
    manual_logistics_calls: int | None = None
    closed_loop_autonomy: bool | None = None
    physical_processing_coverage: float | None = None
    isolated_producers: int | None = None
    fuel_starved_entities: int | None = None
    power_starved_entities: int | None = None

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
        autonomy_score_raw = payload.get("autonomy_score")
        manual_logistics_raw = payload.get("manual_logistics_calls")
        closed_loop_raw = payload.get("closed_loop_autonomy")
        physical_coverage_raw = payload.get("physical_processing_coverage")
        isolated_producers_raw = payload.get("isolated_producers")
        fuel_starved_raw = payload.get("fuel_starved_entities")
        power_starved_raw = payload.get("power_starved_entities")
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
            autonomy_score=(
                float(autonomy_score_raw)
                if isinstance(autonomy_score_raw, (int, float))
                else None
            ),
            manual_logistics_calls=(
                int(manual_logistics_raw)
                if isinstance(manual_logistics_raw, (int, float))
                else None
            ),
            closed_loop_autonomy=(
                bool(closed_loop_raw)
                if isinstance(closed_loop_raw, bool)
                else None
            ),
            physical_processing_coverage=(
                float(physical_coverage_raw)
                if isinstance(physical_coverage_raw, (int, float))
                else None
            ),
            isolated_producers=(
                int(isolated_producers_raw)
                if isinstance(isolated_producers_raw, (int, float))
                else None
            ),
            fuel_starved_entities=(
                int(fuel_starved_raw)
                if isinstance(fuel_starved_raw, (int, float))
                else None
            ),
            power_starved_entities=(
                int(power_starved_raw)
                if isinstance(power_starved_raw, (int, float))
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

    regressions: list[str] = []
    processing_capability = bool(
        {"iron_backbone", "copper_mining", "copper_smelting"}
        & set(challenger.capabilities)
    )
    if (
        processing_capability
        and challenger.physical_processing_coverage is not None
        and challenger.physical_processing_coverage < 0.50
    ):
        regressions.append(
            "physical processing coverage below 50% "
            f"({challenger.physical_processing_coverage:.1%})"
        )
    if (
        "coal_mining" in challenger.capabilities
        and challenger.fuel_starved_entities is not None
        and challenger.fuel_starved_entities > 0
    ):
        regressions.append(
            "fuel starvation remains after coal capability "
            f"({challenger.fuel_starved_entities} entities)"
        )
    if (
        "steam_power" in challenger.capabilities
        and challenger.power_starved_entities is not None
        and challenger.power_starved_entities > 0
    ):
        regressions.append(
            "power starvation remains after steam-power capability "
            f"({challenger.power_starved_entities} entities)"
        )

    if champion is None:
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

    comparable_rates = sorted(
        set(champion.rates_per_s) & set(challenger.rates_per_s)
    )
    rate_improvements: list[str] = []
    for name in comparable_rates:
        baseline = max(0.0, float(champion.rates_per_s[name]))
        candidate = max(0.0, float(challenger.rates_per_s[name]))
        if baseline <= 0:
            if candidate > 0:
                rate_improvements.append(f"{name} established at {candidate:.4g}/s")
            continue
        if candidate >= baseline * throughput_improvement_ratio:
            rate_improvements.append(
                f"{name} improved {baseline:.4g}→{candidate:.4g}/s"
            )

    new_rate_keys = sorted(
        set(challenger.rates_per_s) - set(champion.rates_per_s)
    )
    for name in new_rate_keys:
        candidate = max(0.0, float(challenger.rates_per_s[name]))
        if candidate > 0:
            rate_improvements.append(f"new measured flow {name}={candidate:.4g}/s")

    improvements.extend(rate_improvements)

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

    if (
        champion.manual_logistics_calls is not None
        and challenger.manual_logistics_calls is not None
    ):
        if challenger.manual_logistics_calls > champion.manual_logistics_calls:
            regressions.append(
                "manual logistics increased "
                f"{champion.manual_logistics_calls}→"
                f"{challenger.manual_logistics_calls}"
            )
        elif challenger.manual_logistics_calls < champion.manual_logistics_calls:
            improvements.append(
                "manual logistics reduced "
                f"{champion.manual_logistics_calls}→"
                f"{challenger.manual_logistics_calls}"
            )

    if (
        champion.autonomy_score is not None
        and challenger.autonomy_score is not None
    ):
        if challenger.autonomy_score + 1e-12 < champion.autonomy_score:
            regressions.append(
                "autonomy score regressed "
                f"{champion.autonomy_score:.3f}→"
                f"{challenger.autonomy_score:.3f}"
            )
        elif challenger.autonomy_score > champion.autonomy_score + 1e-12:
            improvements.append(
                "autonomy score improved "
                f"{champion.autonomy_score:.3f}→"
                f"{challenger.autonomy_score:.3f}"
            )

    if (
        champion.physical_processing_coverage is not None
        and challenger.physical_processing_coverage is not None
    ):
        if (
            challenger.physical_processing_coverage + 1e-12
            < champion.physical_processing_coverage
        ):
            regressions.append(
                "physical processing coverage regressed "
                f"{champion.physical_processing_coverage:.1%}→"
                f"{challenger.physical_processing_coverage:.1%}"
            )
        elif (
            challenger.physical_processing_coverage
            > champion.physical_processing_coverage + 1e-12
        ):
            improvements.append(
                "physical processing coverage improved "
                f"{champion.physical_processing_coverage:.1%}→"
                f"{challenger.physical_processing_coverage:.1%}"
            )

    for label, incumbent_value, challenger_value in (
        (
            "isolated producers",
            champion.isolated_producers,
            challenger.isolated_producers,
        ),
        (
            "fuel-starved entities",
            champion.fuel_starved_entities,
            challenger.fuel_starved_entities,
        ),
        (
            "power-starved entities",
            champion.power_starved_entities,
            challenger.power_starved_entities,
        ),
    ):
        if incumbent_value is None or challenger_value is None:
            continue
        if challenger_value > incumbent_value:
            regressions.append(
                f"{label} increased {incumbent_value}→{challenger_value}"
            )
        elif challenger_value < incumbent_value:
            improvements.append(
                f"{label} reduced {incumbent_value}→{challenger_value}"
            )

    if (
        champion.closed_loop_autonomy is True
        and challenger.closed_loop_autonomy is not True
    ):
        regressions.append("closed-loop autonomy was lost")
    elif (
        champion.closed_loop_autonomy is not True
        and challenger.closed_loop_autonomy is True
    ):
        improvements.append("closed-loop autonomy established")

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
    physical_graph: Mapping[str, Any] | None = None,
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
    autonomy_raw = metrics.get("autonomy")
    autonomy = autonomy_raw if isinstance(autonomy_raw, Mapping) else {}
    interventions_raw = metrics.get("interventions")
    interventions = (
        interventions_raw
        if isinstance(interventions_raw, Mapping)
        else {}
    )
    committed_raw = (
        interventions.get("post_bootstrap_committed")
        or interventions.get("committed")
        or {}
    )
    committed = (
        committed_raw
        if isinstance(committed_raw, Mapping)
        else {}
    )
    manual_logistics_raw = autonomy.get("manual_logistics_calls")
    if not isinstance(manual_logistics_raw, (int, float)):
        manual_logistics_raw = committed.get("manual_logistics_calls")

    physical_metrics_raw = (
        physical_graph.get("metrics", {})
        if isinstance(physical_graph, Mapping)
        else {}
    )
    physical_metrics = (
        physical_metrics_raw
        if isinstance(physical_metrics_raw, Mapping)
        else {}
    )

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
        autonomy_score=(
            float(autonomy["score"])
            if isinstance(autonomy.get("score"), (int, float))
            else None
        ),
        manual_logistics_calls=(
            int(manual_logistics_raw)
            if isinstance(manual_logistics_raw, (int, float))
            else None
        ),
        closed_loop_autonomy=(
            bool(autonomy["closed_loop"])
            if isinstance(autonomy.get("closed_loop"), bool)
            else None
        ),
        physical_processing_coverage=(
            float(physical_metrics["physical_processing_coverage"])
            if isinstance(
                physical_metrics.get("physical_processing_coverage"),
                (int, float),
            )
            else None
        ),
        isolated_producers=(
            int(physical_metrics["isolated_producers"])
            if isinstance(physical_metrics.get("isolated_producers"), (int, float))
            else None
        ),
        fuel_starved_entities=(
            int(physical_metrics["fuel_starved_entities"])
            if isinstance(physical_metrics.get("fuel_starved_entities"), (int, float))
            else None
        ),
        power_starved_entities=(
            int(physical_metrics["power_starved_entities"])
            if isinstance(physical_metrics.get("power_starved_entities"), (int, float))
            else None
        ),
    )
