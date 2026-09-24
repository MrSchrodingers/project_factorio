from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from factorio_ai_lab.learning.autonomy import evaluate_factory_autonomy

#: Rates divided by the literal argument of sleep().
RATE_PROTOCOL_SLEEP_LITERAL = "sleep_literal_v1"

#: Rates divided by the game time observed across the step, with the output
#: read off the world production counter.
RATE_PROTOCOL_OBSERVED_WINDOW = "observed_window_v2"

#: Same window, but the output is read from the cell the stage itself built
#: instead of the world counter. In a world inherited from a promoted
#: ancestor the counter also sums machines this generation did not build:
#: measured in generation 46, the belt smelting stage reported 7 plates of
#: its own against 83 on the counter, so 91% of the old figure belonged to
#: the ancestor. The smaller number is the better measurement, which is
#: exactly why it cannot be compared against a floor established by the
#: larger one -- that would reject a challenger for a change of instrument
#: and make the incumbent unbeatable for a reason unrelated to the factory.
RATE_PROTOCOL_CELL_ATTRIBUTED = "cell_attributed_v3"

#: The material moved through machines for the whole measured window: the
#: agent placed and fuelled the cell before the window opened and handled no
#: item inside it. This is the only kind of rate that survives the agent
#: walking away.
RATE_SOURCE_ENDOGENOUS = "endogenous_flow"

#: The agent moved or crafted the material inside the measured window
#: (extract_item/insert_item/craft between machines). A machine may have done
#: the transformation, but the flow stops when the agent stops.
RATE_SOURCE_INTERVENTION = "intervention_batch"

#: Provenance unknown. Not a synonym for either of the two above: an
#: unclassified rate is excluded from both sums, and the sums report None
#: instead of a partial total that would read like a measurement.
RATE_SOURCE_UNCLASSIFIED = "unclassified"

#: Which kind of measurement produced each metric key, read off the stage that
#: writes it in experiments/curriculum_runner.py. A key absent from this table
#: is reported as RATE_SOURCE_UNCLASSIFIED rather than assumed automated.
_RATE_SOURCE_BY_METRIC: Mapping[str, str] = {
    # Belt-fed furnace: inserters feed it and the window holds no agent call.
    "belt_smelting_plate_rate_per_s": RATE_SOURCE_ENDOGENOUS,
    # Drill mining into a chest across a sleep window with no agent call.
    "baseline_iron_rate_per_s": RATE_SOURCE_ENDOGENOUS,
    "scaled_iron_rate_per_s": RATE_SOURCE_ENDOGENOUS,
    "copper_ore_rate_per_s": RATE_SOURCE_ENDOGENOUS,
    # Hand-fed furnace batch: insert_item(ore, coal) and then measure.
    "direct_smelting_plate_rate_per_s": RATE_SOURCE_INTERVENTION,
    "copper_plate_rate_per_s": RATE_SOURCE_INTERVENTION,
    # Assembler whose inputs the agent inserts between rounds.
    "iron_gear_wheel_rate_per_s": RATE_SOURCE_INTERVENTION,
    "automation_science_rate_per_s": RATE_SOURCE_INTERVENTION,
    "logistic_science_rate_per_s": RATE_SOURCE_INTERVENTION,
    # Survival stage: the agent extracts and inserts inside the window.
    "survival_iron_rate_per_s": RATE_SOURCE_INTERVENTION,
    "survival_coal_rate_per_s": RATE_SOURCE_INTERVENTION,
    "survival_copper_rate_per_s": RATE_SOURCE_INTERVENTION,
    # Coal stage refuels the drill with extract/insert inside the window.
    "coal_rate_per_s": RATE_SOURCE_INTERVENTION,
}

#: How productive_runtime_s was obtained: the sum of the stage measurement
#: windows that recorded positive output.
PRODUCTIVE_RUNTIME_STAGE_WINDOWS = "stage_windows_with_output_v1"

#: (duration metric, output metric) pairs whose window is only counted as
#: productive time when the paired output is positive. Stages that record no
#: duration cannot contribute, which is why the sum is a lower bound.
_PRODUCTIVE_WINDOW_METRICS: tuple[tuple[str, str], ...] = (
    ("coal_mining_duration_s", "coal_output"),
    ("direct_smelting_duration_s", "iron_plate_output"),
    ("belt_smelting_duration_s", "belt_smelting_plate_output"),
    ("copper_mining_duration_s", "copper_ore_output"),
    ("copper_smelting_duration_s", "copper_plate_output"),
)



def _optional_name_set(raw: Any) -> frozenset[str] | None:
    """Read a recorded name list, keeping absence distinct from emptiness.

    A fitness vector written before stages were recorded stores nothing for
    them, and that is not the same fact as a generation that was measured and
    completed none. Returning an empty set for both would let the comparison
    draw a verdict from an absence.
    """
    if raw is None:
        return None
    if isinstance(raw, (str, bytes)):
        return None
    if isinstance(raw, Iterable):
        return frozenset(str(item) for item in raw)
    return None

@dataclass(frozen=True)
class InheritedCapabilities:
    """What a generation was handed, and how well that is known.

    Two states would not be enough. Passing ``None`` in place of this object
    means no inheritance was in play, and ``capabilities`` empty means a
    factory was inherited that carried none. The third state is an
    inheritance that was applied and whose capabilities could not be read
    back -- an unreadable checkpoint sidecar, a run_id no record knows.
    Reporting that as either of the other two would credit the genome with
    what it merely received, which is the whole failure
    ``FitnessVector.inherited_capabilities`` exists to prevent, so it is
    carried as ``resolved=False`` plus the reason and the caller withholds
    credit instead of granting it.
    """

    capabilities: frozenset[str] | None
    resolved: bool
    detail: str

    @classmethod
    def resolved_as(
        cls,
        capabilities: Iterable[str],
        *,
        detail: str,
    ) -> InheritedCapabilities:
        """Capabilities read off the record of the run that was inherited."""
        return cls(
            capabilities=frozenset(str(name) for name in capabilities),
            resolved=True,
            detail=detail,
        )

    @classmethod
    def unresolved(cls, detail: str) -> InheritedCapabilities:
        """An inheritance that was applied and could not be read back."""
        return cls(capabilities=None, resolved=False, detail=detail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": self.resolved,
            "detail": self.detail,
            "capabilities": (
                None if self.capabilities is None else sorted(self.capabilities)
            ),
        }


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
    #: Hand calls the repair cycle spent answering a diagnosed failure, kept
    #: out of manual_logistics_calls by the executor that books them (see
    #: integrations/fle.py, execute(purpose="repair")). They are the same
    #: physical act -- the agent carrying coal -- but they exist because a
    #: machine was already measured as starved, so charging them to the
    #: manual-logistics comparison rejects the challenger for the mechanism
    #: that fixed it. Generation 70 is the measured case: the repair drove
    #: fuel_starved_entities from 10 to 0 and the four calls it cost were the
    #: whole difference between generations 69 and 70. Reclassified, never
    #: hidden: the count is carried here and written to the record. `None`
    #: means no repair accounting reached this vector, which is not the same
    #: as a generation that repaired nothing.
    repair_logistics_calls: int | None = None
    closed_loop_autonomy: bool | None = None
    physical_processing_coverage: float | None = None
    isolated_producers: int | None = None
    fuel_starved_entities: int | None = None
    power_starved_entities: int | None = None
    #: How rates_per_s was measured. Rates divided by the literal passed to
    #: sleep() are not comparable with rates divided by the game window that
    #: actually elapsed: the literal inflated them roughly five-fold, and a
    #: floor derived from an inflated value can exceed what the machine is
    #: physically able to produce. A burner mining drill mines 0.25 ore/s; the
    #: generation-6 champion records 0.8125/s for copper ore, which is 3.25x
    #: that. Comparing across protocols would make the incumbent unbeatable
    #: for a reason that has nothing to do with the factory.
    measurement_protocol: str = RATE_PROTOCOL_SLEEP_LITERAL
    #: Provenance of each key in rates_per_s: RATE_SOURCE_ENDOGENOUS for flow
    #: machines sustained on their own, RATE_SOURCE_INTERVENTION for a batch
    #: the agent fed by hand. total_rate_per_s adds both and therefore cannot
    #: tell automation from manual labour. Empty for a fitness recorded before
    #: this field existed, which is why the sums below answer None instead of
    #: crediting an old vector with automation it never demonstrated.
    rate_sources: Mapping[str, str] = field(default_factory=dict)
    #: Capabilities the generation started with, when it inherited a factory
    #: from a promoted ancestor. Absolute counts stop being evidence the moment
    #: a generation begins on top of someone else's work, so a capability that
    #: arrived with the inheritance must not be credited as an achievement of
    #: this genome. `None` means no inheritance was in play, which is not the
    #: same as an empty inheritance.
    inherited_capabilities: frozenset[str] | None = None
    #: How inherited_capabilities was obtained, in readable text: which record
    #: answered it, or why it could not be answered. `None` means no
    #: inheritance was in play. When an inheritance was applied and could not
    #: be resolved, this states the reason and inherited_capabilities holds
    #: every capability the generation reported, so no capability is credited
    #: to the genome on evidence nobody could read.
    inherited_capabilities_source: str | None = None
    #: Seconds of measured production. A lower bound, not the factory lifetime:
    #: it adds the stage windows that recorded output and ignores everything
    #: that was never timed. None means no window was measured at all, 0.0
    #: would mean windows were measured and none of them produced.
    productive_runtime_s: float | None = None
    #: Which protocol produced productive_runtime_s; None when it was not
    #: measured. Two runtimes from different protocols are not comparable, the
    #: same way two rates from different rate protocols are not.
    productive_runtime_source: str | None = None
    #: What was broken in the terminal snapshot -- see
    #: factory_graph.classify_halt_cause. It names the cause, never the moment:
    #: paired with productive_runtime_s it separates a factory that ran and
    #: died from one that was born dead. None means no status was observed.
    halt_cause: str | None = None
    #: Names of the curriculum stages this generation completed, and of the
    #: ones it attempted and failed. Counting failures as an absolute number
    #: compares two generations that did not face the same curriculum: the
    #: generation-6 champion records zero failures because the curriculum
    #: stopped before the hard stage, so every later challenger that reached
    #: stage 13 of 16 and missed the next one read as a regression against a
    #: rule the incumbent was never measured by. Twelve of the thirty-seven
    #: recorded generations were rejected with that as their only regression,
    #: among them every generation that had acquired electronic_circuits and
    #: was not kept. Names
    #: make the comparison commensurate: failing a stage the incumbent
    #: completed is a lost capability, failing one it never completed is the
    #: cost of exploring. `None` means stages were never recorded, which is
    #: not the same as an empty set.
    completed_stages: frozenset[str] | None = None
    failed_stages: frozenset[str] | None = None

    @property
    def total_rate_per_s(self) -> float:
        """
        Every measured rate added together, automation and hand work alike.

        Kept unchanged so the recorded history stays readable; read
        endogenous_rate_per_s when the question is whether the factory
        produces without the agent.
        """
        return sum(max(0.0, float(value)) for value in self.rates_per_s.values())

    @property
    def unclassified_rate_keys(self) -> tuple[str, ...]:
        """Rate keys whose provenance is unknown, sorted."""
        known = {RATE_SOURCE_ENDOGENOUS, RATE_SOURCE_INTERVENTION}
        return tuple(
            sorted(
                key
                for key in self.rates_per_s
                if self.rate_sources.get(key) not in known
            )
        )

    def _rate_sum(self, source: str) -> float | None:
        if self.unclassified_rate_keys:
            # A partial total reads like a measurement of the whole. Refuse it
            # and let the caller see which keys are missing.
            return None
        return sum(
            max(0.0, float(value))
            for key, value in self.rates_per_s.items()
            if self.rate_sources.get(key) == source
        )

    @property
    def endogenous_rate_per_s(self) -> float | None:
        """
        Throughput machines sustained without agent handling.

        0.0 means every measured rate was an intervention batch; None means at
        least one rate has unknown provenance, so no honest sum exists.
        """
        return self._rate_sum(RATE_SOURCE_ENDOGENOUS)

    @property
    def intervention_rate_per_s(self) -> float | None:
        """Throughput that only existed because the agent handled material."""
        return self._rate_sum(RATE_SOURCE_INTERVENTION)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = sorted(self.capabilities)
        payload["inherited_capabilities"] = (
            None
            if self.inherited_capabilities is None
            else sorted(self.inherited_capabilities)
        )
        payload["completed_stages"] = (
            None if self.completed_stages is None else sorted(self.completed_stages)
        )
        payload["failed_stages"] = (
            None if self.failed_stages is None else sorted(self.failed_stages)
        )
        payload["rates_per_s"] = {
            key: round(float(value), 8)
            for key, value in sorted(self.rates_per_s.items())
        }
        payload["total_rate_per_s"] = round(self.total_rate_per_s, 8)
        payload["rate_sources"] = {
            str(key): str(value)
            for key, value in sorted(self.rate_sources.items())
        }
        endogenous = self.endogenous_rate_per_s
        intervention = self.intervention_rate_per_s
        payload["endogenous_rate_per_s"] = (
            None if endogenous is None else round(endogenous, 8)
        )
        payload["intervention_rate_per_s"] = (
            None if intervention is None else round(intervention, 8)
        )
        payload["unclassified_rate_keys"] = list(self.unclassified_rate_keys)
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
        inherited_raw = payload.get("inherited_capabilities")
        inherited_source_raw = payload.get("inherited_capabilities_source")
        inherited_capabilities = (
            None
            if inherited_raw is None
            else frozenset(
                str(value) for value in inherited_raw if isinstance(value, str)
            )
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
        repair_logistics_raw = payload.get("repair_logistics_calls")
        closed_loop_raw = payload.get("closed_loop_autonomy")
        physical_coverage_raw = payload.get("physical_processing_coverage")
        isolated_producers_raw = payload.get("isolated_producers")
        fuel_starved_raw = payload.get("fuel_starved_entities")
        power_starved_raw = payload.get("power_starved_entities")
        sources_raw = payload.get("rate_sources", {})
        # A fitness recorded before provenance existed leaves this empty, so
        # its rates stay unclassified and the endogenous sum stays None.
        rate_sources = (
            {
                str(key): str(value)
                for key, value in sources_raw.items()
                if isinstance(key, str) and isinstance(value, str)
            }
            if isinstance(sources_raw, Mapping)
            else {}
        )
        productive_runtime_raw = payload.get("productive_runtime_s")
        productive_source_raw = payload.get("productive_runtime_source")
        halt_cause_raw = payload.get("halt_cause")
        # A fitness recorded before this field existed came from the
        # sleep-literal denominator, so that is the honest default.
        protocol = str(
            payload.get("measurement_protocol")
            or RATE_PROTOCOL_SLEEP_LITERAL
        )
        return cls(
            measurement_protocol=protocol,
            inherited_capabilities=inherited_capabilities,
            inherited_capabilities_source=(
                str(inherited_source_raw)
                if isinstance(inherited_source_raw, str)
                else None
            ),
            rate_sources=rate_sources,
            productive_runtime_s=(
                float(productive_runtime_raw)
                if isinstance(productive_runtime_raw, (int, float))
                else None
            ),
            productive_runtime_source=(
                str(productive_source_raw)
                if isinstance(productive_source_raw, str)
                else None
            ),
            halt_cause=(
                str(halt_cause_raw)
                if isinstance(halt_cause_raw, str)
                else None
            ),
            capabilities=capabilities,
            rates_per_s=rates,
            external_dependencies=int(payload.get("external_dependencies", 0) or 0),
            failures=int(payload.get("failures", 0) or 0),
            completed_stages=_optional_name_set(payload.get("completed_stages")),
            failed_stages=_optional_name_set(payload.get("failed_stages")),
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
            repair_logistics_calls=(
                int(repair_logistics_raw)
                if isinstance(repair_logistics_raw, (int, float))
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


_NEW_METRIC_REASON = (
    "incumbent was never measured for it; new metric, floor is set by the "
    "first challenger promoted on commensurate evidence"
)


@dataclass(frozen=True)
class PromotionDecision:
    promoted: bool
    reason: str
    regressions: tuple[str, ...]
    improvements: tuple[str, ...]
    retention_ratio: float
    compared_metrics: tuple[str, ...] = ()
    incommensurable_metrics: tuple[str, ...] = ()
    #: Absolute floors that were not enforced because the incumbent does not
    #: satisfy them either, one entry per floor with the incumbent's own
    #: reading. Distinct from incommensurable_metrics: there the two sides
    #: were measured by different instruments and no verdict exists; here both
    #: were measured by the same instrument, the comparative verdict is still
    #: drawn, and only the absolute floor steps back. Silence would leave no
    #: way to tell a floor that held from one that was never applied.
    withheld_gates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "promoted": self.promoted,
            "reason": self.reason,
            "regressions": list(self.regressions),
            "improvements": list(self.improvements),
            "retention_ratio": self.retention_ratio,
            "compared_metrics": list(self.compared_metrics),
            "incommensurable_metrics": list(self.incommensurable_metrics),
            "withheld_gates": list(self.withheld_gates),
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

    Constraints on optional metrics are only enforced against an incumbent that
    was measured by the same instrument; see ``_commensurate`` below. Absolute
    floors are only enforced against a challenger when the incumbent satisfies
    them too; see ``_floor_enforceable``. The decision reports which metrics
    carried the comparison, which were left out as incommensurable, and which
    floors were withheld.
    """
    if not 0 < retention_ratio <= 1:
        raise ValueError("retention_ratio must be in (0, 1]")
    if throughput_improvement_ratio < 1:
        raise ValueError("throughput_improvement_ratio must be >= 1")

    regressions: list[str] = []
    compared_metrics: list[str] = []
    incommensurable_metrics: list[str] = []
    withheld_gates: list[str] = []

    def _mark_compared(metric: str) -> None:
        if metric not in compared_metrics:
            compared_metrics.append(metric)

    def _mark_incommensurable(metric: str, reason: str) -> None:
        entry = f"{metric}: {reason}"
        if entry not in incommensurable_metrics:
            incommensurable_metrics.append(entry)

    rate_protocols_match = (
        champion is None
        or champion.measurement_protocol == challenger.measurement_protocol
    )

    def _commensurate(metric: str, challenger_value: Any) -> bool:
        """
        Report whether `metric` may carry the incumbent/challenger comparison.

        Optional metrics entered the fitness vector generation by generation,
        so an incumbent promoted before a metric existed stores None for it.
        Rejecting a challenger on such a metric enforces a rule the incumbent
        itself was never measured by, which makes the incumbent unbeatable
        instead of merely hard to beat. A metric therefore participates in the
        comparison, as rejection ground or as credit, only when both sides were
        measured by the same instrument. When the incumbent lacks it the metric
        is reported as incommensurable and no verdict is drawn from it; the
        floor is established by the first challenger promoted on commensurate
        evidence, because the promoted fitness vector is stored whole and
        carries the new metric into the next comparison.
        """
        if champion is None:
            # No incumbent: the absolute survival floors below decide the first
            # champion and have nothing to be commensurate with.
            return challenger_value is not None
        if challenger_value is None:
            _mark_incommensurable(metric, "challenger did not measure it")
            return False
        if getattr(champion, metric) is None:
            _mark_incommensurable(metric, _NEW_METRIC_REASON)
            return False
        _mark_compared(metric)
        return True

    def _floor_enforceable(
        metric: str,
        *,
        incumbent_value: Any,
        incumbent_satisfies: bool,
        standard: str,
    ) -> bool:
        """
        Report whether an absolute floor may reject the challenger.

        The floors below state what a factory has to be, not what it has to
        beat, which is why they read as absolute. An incumbent that does not
        satisfy one of them was promoted without ever having to: enforcing it
        against the challenger alone is a rule only one side was measured by,
        the same shape as the `failures` count this module already stopped
        comparing. Measured on the incumbent of generation 37: coverage 16.7%
        against a 50% floor and one fuel-starved entity against a zero floor,
        so the incumbent fails both floors it rejected generations 44 to 73
        with, and thirty-six generations produced no promotion.

        The floor is not removed. It is withheld only while the incumbent
        fails it, and comes back the moment a promoted champion satisfies it
        -- the same way `_commensurate` lets the first challenger promoted on
        a new metric establish its floor. What still rejects a challenger in
        the meantime is the comparative reading of the same metric: coverage
        that drops below the incumbent's, starvation that rises above it.
        A challenger that wrecks the factory is therefore still rejected, and
        one that improves it while both sides remain short of the floor is
        not.
        """
        if champion is None:
            # No incumbent: the floors are the baseline survival gates and
            # there is nobody they could be unfair to.
            return True
        if incumbent_satisfies:
            return True
        rendered = (
            f"{incumbent_value:.4g}"
            if isinstance(incumbent_value, float)
            else str(incumbent_value)
        )
        entry = (
            f"{metric}: absolute floor ({standard}) withheld; the incumbent "
            f"does not satisfy it either (incumbent {rendered})"
        )
        if entry not in withheld_gates:
            withheld_gates.append(entry)
        return False

    incumbent_coverage = (
        None if champion is None else champion.physical_processing_coverage
    )
    incumbent_fuel_starved = (
        None if champion is None else champion.fuel_starved_entities
    )
    incumbent_power_starved = (
        None if champion is None else champion.power_starved_entities
    )

    processing_capability = bool(
        {"iron_backbone", "copper_mining", "copper_smelting"}
        & set(challenger.capabilities)
    )
    if (
        processing_capability
        and _commensurate(
            "physical_processing_coverage",
            challenger.physical_processing_coverage,
        )
        and challenger.physical_processing_coverage < 0.50
        and _floor_enforceable(
            "physical_processing_coverage",
            incumbent_value=incumbent_coverage,
            incumbent_satisfies=(
                incumbent_coverage is not None and incumbent_coverage >= 0.50
            ),
            standard="physical processing coverage of at least 50%",
        )
    ):
        regressions.append(
            "physical processing coverage below 50% "
            f"({challenger.physical_processing_coverage:.1%})"
        )
    if (
        "coal_mining" in challenger.capabilities
        and _commensurate("fuel_starved_entities", challenger.fuel_starved_entities)
        and challenger.fuel_starved_entities > 0
        and _floor_enforceable(
            "fuel_starved_entities",
            incumbent_value=incumbent_fuel_starved,
            incumbent_satisfies=(
                incumbent_fuel_starved is not None and incumbent_fuel_starved == 0
            ),
            standard="no fuel-starved entity after the coal capability",
        )
    ):
        regressions.append(
            "fuel starvation remains after coal capability "
            f"({challenger.fuel_starved_entities} entities)"
        )
    if (
        "steam_power" in challenger.capabilities
        and _commensurate("power_starved_entities", challenger.power_starved_entities)
        and challenger.power_starved_entities > 0
        and _floor_enforceable(
            "power_starved_entities",
            incumbent_value=incumbent_power_starved,
            incumbent_satisfies=(
                incumbent_power_starved is not None and incumbent_power_starved == 0
            ),
            standard="no power-starved entity after the steam-power capability",
        )
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

    # Present on every fitness vector, so always commensurate.
    _mark_compared("capabilities")
    if rate_protocols_match:
        _mark_compared("rates_per_s")
    _mark_compared("external_dependencies")
    # `failures` is deliberately not marked here. The raw count no longer
    # carries any verdict -- the named-stage comparison below does -- and
    # compared_metrics is read back from evolution_history.jsonl as the record
    # of what decided the promotion. Naming a metric that decided nothing
    # makes the record lie about its own reasoning.

    lost_capabilities = sorted(champion.capabilities - challenger.capabilities)
    if lost_capabilities:
        regressions.append(
            "lost capabilities: " + ", ".join(lost_capabilities)
        )

    if not rate_protocols_match:
        # The incumbent's rates were produced by a different measurement, so
        # its floors describe a quantity the challenger never reported. Say so
        # and skip, rather than reject against a number that is not comparable.
        _mark_incommensurable(
            "rates_per_s",
            f"incumbent measured by {champion.measurement_protocol}, "
            f"challenger by {challenger.measurement_protocol}; floors from a "
            "different instrument cannot be enforced",
        )
    else:
        for name, baseline in champion.rates_per_s.items():
            baseline_value = max(0.0, float(baseline))
            if baseline_value <= 0:
                continue
            if name not in challenger.rates_per_s:
                regressions.append(
                    f"{name} rate was not measured; retention cannot be demonstrated"
                )
                continue
            challenger_value = max(
                0.0,
                float(challenger.rates_per_s[name]),
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

    # A failed stage is a regression only when the incumbent demonstrated that
    # the stage was reachable. Comparing the raw counts punishes advancing:
    # the incumbent completed a shorter curriculum, so it carries zero
    # failures, and any challenger that goes further and misses the next stage
    # reads as worse than one that attempts nothing new. Twelve of the
    # thirty-seven recorded generations were rejected on that count alone.
    champion_completed = champion.completed_stages
    challenger_completed = challenger.completed_stages
    challenger_failed = challenger.failed_stages
    if (
        champion_completed is not None
        and challenger_completed is not None
        and challenger_failed is not None
    ):
        _mark_compared("stages")
        lost_stages = sorted(challenger_failed & champion_completed)
        if lost_stages:
            regressions.append(
                "stages the incumbent completed now fail: "
                + ", ".join(lost_stages)
            )
        missing_stages = sorted(
            champion_completed - challenger_completed - challenger_failed
        )
        if missing_stages:
            regressions.append(
                "stages the incumbent completed were not reached: "
                + ", ".join(missing_stages)
            )
        advanced_stages = sorted(challenger_completed - champion_completed)
        if advanced_stages:
            improvements.append(
                "frontier stages completed: " + ", ".join(advanced_stages)
            )
    else:
        # Counting alone cannot carry the comparison, and this is exactly the
        # axis where substituting a count for the missing names produced the
        # false verdict. The absence is reported instead of guessed; the floor
        # is established by the first challenger promoted on named evidence,
        # whose fitness vector is stored whole and carries the names forward.
        _mark_incommensurable(
            "stages",
            "stage names were not recorded on both sides; failure counts from "
            "different curricula are not comparable",
        )

    # A capability that arrived with an inherited factory is not an
    # achievement of this genome. Crediting it would make every heir look like
    # a breakthrough on its first generation, which is the failure mode that
    # persistence introduces: the system would report learning where it only
    # reported inheritance. Losing a capability still counts as a regression
    # above, inherited or not, because an heir that destroys what it received
    # is genuinely worse.
    new_capabilities = sorted(challenger.capabilities - champion.capabilities)
    inherited = challenger.inherited_capabilities
    if inherited is not None:
        credited = [name for name in new_capabilities if name not in inherited]
        withheld = [name for name in new_capabilities if name in inherited]
        if withheld:
            source = challenger.inherited_capabilities_source
            _mark_incommensurable(
                "inherited_capabilities",
                "not credited as achievements of this genome: "
                + ", ".join(withheld)
                + (f" (inheritance: {source})" if source else ""),
            )
        new_capabilities = credited
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
        _commensurate("route_cost", challenger.route_cost)
        and challenger.route_cost + 1e-9 < champion.route_cost
    ):
        improvements.append(
            f"route cost improved {champion.route_cost:.4g}→"
            f"{challenger.route_cost:.4g}"
        )

    if (
        _commensurate("route_turns", challenger.route_turns)
        and challenger.route_turns < champion.route_turns
    ):
        improvements.append(
            f"route turns improved {champion.route_turns}→"
            f"{challenger.route_turns}"
        )

    if _commensurate("manual_logistics_calls", challenger.manual_logistics_calls):
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

    if _commensurate("autonomy_score", challenger.autonomy_score):
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

    if _commensurate(
        "physical_processing_coverage",
        challenger.physical_processing_coverage,
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

    for label, metric, incumbent_value, challenger_value in (
        (
            "isolated producers",
            "isolated_producers",
            champion.isolated_producers,
            challenger.isolated_producers,
        ),
        (
            "fuel-starved entities",
            "fuel_starved_entities",
            champion.fuel_starved_entities,
            challenger.fuel_starved_entities,
        ),
        (
            "power-starved entities",
            "power_starved_entities",
            champion.power_starved_entities,
            challenger.power_starved_entities,
        ),
    ):
        if not _commensurate(metric, challenger_value):
            continue
        if challenger_value > incumbent_value:
            regressions.append(
                f"{label} increased {incumbent_value}→{challenger_value}"
            )
        elif challenger_value < incumbent_value:
            improvements.append(
                f"{label} reduced {incumbent_value}→{challenger_value}"
            )

    # Closed-loop autonomy keeps the reading already applied to production
    # rates: not reported by the challenger means not demonstrated, so an
    # incumbent that demonstrated it is not overtaken by a silent challenger.
    # An incumbent that was never measured for it stays incommensurable.
    if champion.closed_loop_autonomy is None:
        _mark_incommensurable("closed_loop_autonomy", _NEW_METRIC_REASON)
    elif champion.closed_loop_autonomy is True:
        _mark_compared("closed_loop_autonomy")
        if challenger.closed_loop_autonomy is not True:
            regressions.append("closed-loop autonomy was lost")
    else:
        _mark_compared("closed_loop_autonomy")
        if challenger.closed_loop_autonomy is True:
            improvements.append("closed-loop autonomy established")

    if regressions:
        return PromotionDecision(
            promoted=False,
            reason="challenger rejected by survival constraints",
            regressions=tuple(regressions),
            improvements=tuple(improvements),
            retention_ratio=retention_ratio,
            compared_metrics=tuple(compared_metrics),
            incommensurable_metrics=tuple(incommensurable_metrics),
            withheld_gates=tuple(withheld_gates),
        )

    if improvements:
        return PromotionDecision(
            promoted=True,
            reason="challenger survives incumbent constraints and improves the frontier",
            regressions=(),
            improvements=tuple(improvements),
            retention_ratio=retention_ratio,
            compared_metrics=tuple(compared_metrics),
            incommensurable_metrics=tuple(incommensurable_metrics),
            withheld_gates=tuple(withheld_gates),
        )

    return PromotionDecision(
        promoted=False,
        reason="challenger survives but does not dominate the incumbent",
        regressions=(),
        improvements=(),
        retention_ratio=retention_ratio,
        compared_metrics=tuple(compared_metrics),
        incommensurable_metrics=tuple(incommensurable_metrics),
        withheld_gates=tuple(withheld_gates),
    )


def _snapshot_from_physical_graph(
    physical_graph: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """
    Rebuild the entity rows the autonomy evaluator reads from graph nodes.

    build_factory_graph keeps name, position and status for every entity it
    could place, which is exactly the evidence evaluate_factory_autonomy needs.
    """
    nodes = (
        physical_graph.get("nodes")
        if isinstance(physical_graph, Mapping)
        else None
    )
    if not isinstance(nodes, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        name = node.get("name")
        x = node.get("x")
        y = node.get("y")
        if not isinstance(name, str):
            continue
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            continue
        rows.append(
            {
                "name": name,
                "position": {"x": float(x), "y": float(y)},
                "status": node.get("status"),
            }
        )
    return rows


def _autonomy_from_physical_graph(
    physical_graph: Mapping[str, Any] | None,
    *,
    production_rates_per_s: Mapping[str, float],
    interventions: Mapping[str, int] | None,
    soak_runtime_s: float | None,
) -> Mapping[str, Any]:
    """
    Measure autonomy from the terminal snapshot when no soak payload exists.

    Returns an empty mapping when no snapshot reached this builder: autonomy
    then stays unmeasured (None in the fitness vector), which is not the same
    as measured at zero.
    """
    rows = _snapshot_from_physical_graph(physical_graph)
    if not rows:
        return {}
    return evaluate_factory_autonomy(
        entities=rows,
        interventions=interventions,
        production_rates_per_s=production_rates_per_s,
        soak_runtime_s=soak_runtime_s,
    ).to_dict()


def fitness_from_research(
    *,
    metrics: Mapping[str, Any],
    achieved: set[str] | frozenset[str],
    resource_accounting: Mapping[str, Any] | None = None,
    physical_graph: Mapping[str, Any] | None = None,
    failed_stages: int = 0,
    completed_stage_names: Iterable[str] | None = None,
    failed_stage_names: Iterable[str] | None = None,
    inherited_capabilities: InheritedCapabilities | None = None,
) -> FitnessVector:
    rates: dict[str, float] = {}
    rate_sources: dict[str, str] = {}

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
                rate_sources[item] = _RATE_SOURCE_BY_METRIC.get(
                    key,
                    RATE_SOURCE_UNCLASSIFIED,
                )
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
    # The executor books repair steps in their own counter, so they are
    # already absent from `committed` above and from the manual-logistics
    # comparison. Read here so the count stays visible in the record: a
    # reclassification that disappears from the evidence is indistinguishable
    # from a number nobody measured.
    repair_committed_raw = interventions.get("committed_repair")
    repair_committed = (
        repair_committed_raw
        if isinstance(repair_committed_raw, Mapping)
        else {}
    )
    repair_logistics_raw = repair_committed.get("manual_logistics_calls")
    soak_raw = metrics.get("autonomy_soak_runtime_s")
    soak_runtime_s = (
        float(soak_raw)
        if isinstance(soak_raw, (int, float))
        else None
    )
    autonomy_raw = metrics.get("autonomy")
    autonomy: Mapping[str, Any] = (
        autonomy_raw if isinstance(autonomy_raw, Mapping) else {}
    )
    if not autonomy:
        # A generation that never ran the autonomy soak carries no autonomy
        # payload, and both autonomy fields stayed null in every report so
        # far. The terminal snapshot behind physical_graph is the same
        # evidence the soak reads minus the window itself, so evaluating it
        # here is what turns the score into a measurement. The missing window
        # is reported as an unmeasured gate, never as a zero.
        autonomy = _autonomy_from_physical_graph(
            physical_graph,
            production_rates_per_s=rates,
            interventions=committed or None,
            soak_runtime_s=soak_runtime_s,
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

    # Lower bound on productive time: only windows that were timed and that
    # recorded output are added. What is missing to measure the real lifetime
    # is a status time series (or a first-failure tick); a single terminal
    # snapshot cannot date the failure it reports, so no number is invented
    # for it here.
    productive_runtime_s: float | None = None
    for duration_key, output_key in _PRODUCTIVE_WINDOW_METRICS:
        duration = metrics.get(duration_key)
        output = metrics.get(output_key)
        if not isinstance(duration, (int, float)) or float(duration) <= 0:
            continue
        if not isinstance(output, (int, float)) or float(output) <= 0:
            continue
        productive_runtime_s = (productive_runtime_s or 0.0) + float(duration)
    halt_cause_raw = physical_metrics.get("halt_cause")

    capabilities = frozenset(str(item) for item in achieved)
    # An inheritance that was applied and could not be read back withholds
    # every capability instead of crediting every capability. Leaving the
    # field at None would make the comparison charge the ancestor's factory to
    # this genome, and the field has no third value meaning unknown, so the
    # mistake is taken on the side that awards no progress and
    # inherited_capabilities_source carries why.
    inherited_names: frozenset[str] | None = None
    inherited_source: str | None = None
    if inherited_capabilities is not None:
        inherited_source = inherited_capabilities.detail
        inherited_names = (
            inherited_capabilities.capabilities
            if (
                inherited_capabilities.resolved
                and inherited_capabilities.capabilities is not None
            )
            else capabilities
        )

    return FitnessVector(
        # Built from the observed game window, not from the sleep literal.
        measurement_protocol=RATE_PROTOCOL_CELL_ATTRIBUTED,
        capabilities=capabilities,
        inherited_capabilities=inherited_names,
        inherited_capabilities_source=inherited_source,
        rates_per_s=rates,
        rate_sources=rate_sources,
        productive_runtime_s=productive_runtime_s,
        productive_runtime_source=(
            PRODUCTIVE_RUNTIME_STAGE_WINDOWS
            if productive_runtime_s is not None
            else None
        ),
        halt_cause=(
            str(halt_cause_raw)
            if isinstance(halt_cause_raw, str)
            else None
        ),
        external_dependencies=external_dependencies,
        failures=max(0, int(failed_stages)),
        completed_stages=(
            None
            if completed_stage_names is None
            else frozenset(str(name) for name in completed_stage_names)
        ),
        failed_stages=(
            None
            if failed_stage_names is None
            else frozenset(str(name) for name in failed_stage_names)
        ),
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
        repair_logistics_calls=(
            int(repair_logistics_raw)
            if isinstance(repair_logistics_raw, (int, float))
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
