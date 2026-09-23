import math
import unittest
from statistics import NormalDist

from factorio_ai_lab.learning.survival_analysis import (
    CAUSE_UNSPECIFIED,
    CENSORING_HALT_CAUSE,
    DISPOSITION_CENSORED,
    DISPOSITION_EVENT,
    DISPOSITION_INELIGIBLE,
    DISPOSITION_UNKNOWN,
    VERDICT_EMPTY,
    VERDICT_INSUFFICIENT,
    Observation,
    cause_specific_kaplan_meier,
    classify_halt_cause,
    competing_risks,
    describe_by_stratum,
    hazard_table,
    kaplan_meier,
    observations_from_generation_reports,
    required_events_for_hazard_ratio,
)

# Canonical worked example: 21 subjects, 9 deaths and 12 right-censored
# observations. The expected survival values below are not copied from any
# source: each one is the explicit product of the (n_j - d_j) / n_j factors
# derived by hand from the risk sets, so the test validates the estimator
# rather than restating it.
CANONICAL_EVENTS = (6.0, 6.0, 6.0, 7.0, 10.0, 13.0, 16.0, 22.0, 23.0)
CANONICAL_CENSORED = (6.0, 9.0, 10.0, 11.0, 17.0, 19.0, 20.0, 25.0, 32.0, 32.0, 34.0, 35.0)

# Risk set immediately before each event time, derived by hand. Ties between an
# event and a censoring at the same instant keep the censored subject at risk.
CANONICAL_AT_RISK = (21, 17, 15, 12, 11, 7, 6)
CANONICAL_EVENT_TIMES = (6.0, 7.0, 10.0, 13.0, 16.0, 22.0, 23.0)


def _canonical_observations() -> list[Observation]:
    events = [Observation(time_s=t, event=True, cause="death") for t in CANONICAL_EVENTS]
    censored = [Observation(time_s=t, event=False) for t in CANONICAL_CENSORED]
    return events + censored


def _canonical_expected_survival() -> list[float]:
    factors = ((18, 21), (16, 17), (14, 15), (11, 12), (10, 11), (6, 7), (5, 6))
    survival = 1.0
    out = []
    for numerator, denominator in factors:
        survival *= numerator / denominator
        out.append(survival)
    return out


class KaplanMeierTests(unittest.TestCase):
    def test_canonical_curve_matches_hand_computed_product(self) -> None:
        curve = kaplan_meier(_canonical_observations())
        self.assertEqual(tuple(p.time_s for p in curve.points[1:]), CANONICAL_EVENT_TIMES)
        self.assertEqual(tuple(p.n_at_risk for p in curve.points[1:]), CANONICAL_AT_RISK)
        for point, expected in zip(curve.points[1:], _canonical_expected_survival(), strict=True):
            self.assertAlmostEqual(point.survival, expected, places=12)
        self.assertEqual(curve.points[0].time_s, 0.0)
        self.assertEqual(curve.points[0].survival, 1.0)
        self.assertEqual(curve.gate.n, 21)
        self.assertEqual(curve.gate.n_events, 9)
        self.assertEqual(curve.gate.n_censored, 12)

    def test_censoring_changes_the_curve(self) -> None:
        # Treating the 12 censored subjects as deaths is the bias this estimator
        # exists to avoid: it pushes survival down. If both curves agreed the
        # test would prove nothing, so the inequality is asserted explicitly.
        correct = kaplan_meier(_canonical_observations())
        as_deaths = [
            Observation(time_s=o.time_s, event=True, cause="death")
            for o in _canonical_observations()
        ]
        naive = kaplan_meier(as_deaths)
        correct_at_23 = correct.survival_at(23.0)
        naive_at_23 = naive.survival_at(23.0)
        assert correct_at_23 is not None and naive_at_23 is not None
        self.assertAlmostEqual(correct_at_23, 0.448179271708683, places=12)
        self.assertAlmostEqual(naive_at_23, 5 / 21, places=12)
        self.assertLess(naive_at_23, correct_at_23)
        self.assertNotAlmostEqual(naive_at_23, correct_at_23, places=3)
        # Without censoring the Kaplan-Meier estimator degenerates to the
        # empirical survival function, which is another way to see the damage.
        self.assertAlmostEqual(naive.survival_at(6.0), 17 / 21, places=12)

    def test_survival_is_undefined_beyond_follow_up(self) -> None:
        curve = kaplan_meier(_canonical_observations())
        self.assertEqual(curve.follow_up_end_s, 35.0)
        self.assertIsNone(curve.survival_at(35.1))
        self.assertIsNone(curve.survival_at(-1.0))
        self.assertAlmostEqual(curve.survival_at(5.9), 1.0, places=12)

    def test_confidence_interval_brackets_the_estimate(self) -> None:
        curve = kaplan_meier(_canonical_observations(), confidence=0.95)
        self.assertEqual(curve.confidence_level, 0.95)
        for point in curve.points[1:]:
            if point.ci_low is None or point.ci_high is None:
                continue
            self.assertLessEqual(point.ci_low, point.survival)
            self.assertLessEqual(point.survival, point.ci_high)
            self.assertGreaterEqual(point.ci_low, 0.0)
            self.assertLessEqual(point.ci_high, 1.0)

    def test_median_is_reported_only_when_the_curve_reaches_one_half(self) -> None:
        curve = kaplan_meier(_canonical_observations())
        self.assertEqual(curve.median_survival_s, 23.0)
        never = kaplan_meier(
            [
                Observation(time_s=10.0, event=True, cause="death"),
                Observation(time_s=20.0, event=False),
                Observation(time_s=30.0, event=False),
                Observation(time_s=40.0, event=False),
            ]
        )
        self.assertIsNone(never.median_survival_s)
        self.assertIn("never", never.median_survival_reason)

    def test_empty_sample_refuses_to_produce_an_estimate(self) -> None:
        curve = kaplan_meier([])
        self.assertEqual(curve.gate.verdict, VERDICT_EMPTY)
        self.assertFalse(curve.gate.sufficient)
        self.assertIsNone(curve.survival_at(1.0))
        self.assertIsNone(curve.median_survival_s)


class HazardTests(unittest.TestCase):
    def test_conditional_hazard_and_nelson_aalen(self) -> None:
        observations = [
            Observation(time_s=1.0, event=True, cause="a"),
            Observation(time_s=2.0, event=True, cause="b"),
            Observation(time_s=3.0, event=False),
            Observation(time_s=4.0, event=True, cause="a"),
        ]
        table = hazard_table(observations)
        hazards = [p.conditional_hazard for p in table.points]
        self.assertEqual(len(hazards), 3)
        self.assertAlmostEqual(hazards[0], 1 / 4, places=12)
        self.assertAlmostEqual(hazards[1], 1 / 3, places=12)
        self.assertAlmostEqual(hazards[2], 1.0, places=12)
        cumulative = [p.cumulative_hazard for p in table.points]
        self.assertAlmostEqual(cumulative[-1], 1 / 4 + 1 / 3 + 1.0, places=12)
        # Rate per unit time uses the width of the interval that starts at the
        # event time; the last interval has no width and reports None.
        self.assertAlmostEqual(table.points[0].hazard_rate_per_s, (1 / 4) / 1.0, places=12)
        self.assertAlmostEqual(table.points[1].hazard_rate_per_s, (1 / 3) / 2.0, places=12)
        self.assertIsNone(table.points[-1].hazard_rate_per_s)
        self.assertEqual(table.gate.n_events, 3)
        self.assertEqual(table.gate.n_censored, 1)

    def test_hazard_separates_early_from_late_death_when_means_agree(self) -> None:
        # Two samples with the same mean runtime but opposite timing profiles.
        early = [
            Observation(time_s=10.0, event=True, cause="a"),
            Observation(time_s=10.0, event=True, cause="a"),
            Observation(time_s=90.0, event=True, cause="a"),
            Observation(time_s=90.0, event=True, cause="a"),
        ]
        late = [
            Observation(time_s=40.0, event=True, cause="a"),
            Observation(time_s=50.0, event=True, cause="a"),
            Observation(time_s=55.0, event=True, cause="a"),
            Observation(time_s=55.0, event=True, cause="a"),
        ]
        mean_early = sum(o.time_s for o in early) / 4
        mean_late = sum(o.time_s for o in late) / 4
        self.assertAlmostEqual(mean_early, mean_late, places=12)
        early_first = hazard_table(early).points[0]
        late_first = hazard_table(late).points[0]
        self.assertGreater(early_first.conditional_hazard, late_first.conditional_hazard)
        self.assertLess(early_first.time_s, late_first.time_s)


class CompetingRisksTests(unittest.TestCase):
    def _sample(self) -> list[Observation]:
        return [
            Observation(time_s=1.0, event=True, cause="fuel_starvation"),
            Observation(time_s=2.0, event=True, cause="power_starvation"),
            Observation(time_s=3.0, event=False),
            Observation(time_s=4.0, event=True, cause="fuel_starvation"),
        ]

    def test_cumulative_incidence_matches_hand_computation(self) -> None:
        result = competing_risks(self._sample())
        fuel = result.incidence_at("fuel_starvation", 4.0)
        power = result.incidence_at("power_starvation", 4.0)
        self.assertAlmostEqual(fuel, 0.25 + 0.5 * 1.0, places=12)
        self.assertAlmostEqual(power, 0.75 * (1 / 3), places=12)
        self.assertAlmostEqual(result.incidence_at("fuel_starvation", 1.0), 0.25, places=12)
        self.assertAlmostEqual(result.incidence_at("power_starvation", 1.0), 0.0, places=12)

    def test_incidences_sum_to_one_minus_survival(self) -> None:
        result = competing_risks(self._sample())
        for time_s in (1.0, 2.0, 3.0, 4.0):
            survival = result.overall.survival_at(time_s)
            assert survival is not None
            self.assertAlmostEqual(result.total_incidence_at(time_s), 1.0 - survival, places=12)

    def test_one_minus_cause_specific_km_overstates_incidence(self) -> None:
        # Treating the competing cause as censoring is the classic mistake: the
        # complement of the cause-specific Kaplan-Meier curve is larger than the
        # cumulative incidence because it assumes the competing death could have
        # been followed to a fuel death.
        result = competing_risks(self._sample())
        naive_curve = cause_specific_kaplan_meier(self._sample(), "fuel_starvation")
        naive_survival = naive_curve.survival_at(4.0)
        assert naive_survival is not None
        naive_incidence = 1.0 - naive_survival
        self.assertAlmostEqual(naive_incidence, 1.0, places=12)
        self.assertGreater(naive_incidence, result.incidence_at("fuel_starvation", 4.0))

    def test_single_cause_without_censoring_reduces_to_one_minus_survival(self) -> None:
        observations = [
            Observation(time_s=620.4, event=True, cause="fuel_and_power_starvation"),
            Observation(time_s=680.4, event=True, cause="fuel_and_power_starvation"),
            Observation(time_s=680.4, event=True, cause="fuel_and_power_starvation"),
            Observation(time_s=680.4, event=True, cause="fuel_and_power_starvation"),
        ]
        result = competing_risks(observations)
        self.assertAlmostEqual(
            result.incidence_at("fuel_and_power_starvation", 620.4), 0.25, places=12
        )
        self.assertAlmostEqual(
            result.incidence_at("fuel_and_power_starvation", 680.4), 1.0, places=12
        )
        self.assertEqual(result.gate.verdict, VERDICT_INSUFFICIENT)

    def test_events_without_a_cause_are_grouped_and_flagged(self) -> None:
        observations = [
            Observation(time_s=1.0, event=True),
            Observation(time_s=2.0, event=True, cause="fuel_starvation"),
        ]
        result = competing_risks(observations)
        self.assertIn(CAUSE_UNSPECIFIED, result.cause_counts)
        self.assertEqual(result.cause_counts[CAUSE_UNSPECIFIED], 1)


class HaltCauseMappingTests(unittest.TestCase):
    def test_classification_of_each_halt_cause(self) -> None:
        self.assertEqual(classify_halt_cause("fuel_starvation"), DISPOSITION_EVENT)
        self.assertEqual(classify_halt_cause("power_starvation"), DISPOSITION_EVENT)
        self.assertEqual(classify_halt_cause("fuel_and_power_starvation"), DISPOSITION_EVENT)
        self.assertEqual(classify_halt_cause(CENSORING_HALT_CAUSE), DISPOSITION_CENSORED)
        self.assertEqual(classify_halt_cause("no_factory"), DISPOSITION_INELIGIBLE)
        self.assertEqual(classify_halt_cause(None), DISPOSITION_UNKNOWN)
        self.assertEqual(classify_halt_cause("something_new"), DISPOSITION_UNKNOWN)

    def test_extraction_keeps_uninstrumented_generations_out_of_the_sample(self) -> None:
        reports = [
            {
                "generation": 31,
                "challenger": {"fitness": {"productive_runtime_s": None, "halt_cause": None}},
            },
            {
                "generation": 32,
                "challenger": {
                    "configuration": {"coal_safety_stock": 2, "placement_best_arm": "east_near"},
                    "fitness": {
                        "productive_runtime_s": 680.4,
                        "halt_cause": "fuel_and_power_starvation",
                    },
                },
            },
            {
                "generation": 33,
                "challenger": {
                    "configuration": {"coal_safety_stock": 4},
                    "fitness": {"productive_runtime_s": 620.4, "halt_cause": "none_observed"},
                },
            },
            {
                "generation": 34,
                "challenger": {"fitness": {"productive_runtime_s": 0.0, "halt_cause": "no_factory"}},
            },
        ]
        extraction = observations_from_generation_reports(reports)
        self.assertEqual(len(extraction.observations), 2)
        labels = [o.label for o in extraction.observations]
        self.assertEqual(labels, ["generation-32", "generation-33"])
        self.assertTrue(extraction.observations[0].event)
        self.assertFalse(extraction.observations[1].event)
        self.assertEqual(extraction.excluded[DISPOSITION_UNKNOWN], 1)
        self.assertEqual(extraction.excluded[DISPOSITION_INELIGIBLE], 1)
        self.assertEqual(extraction.gate.n, 2)
        self.assertEqual(extraction.gate.n_events, 1)
        self.assertEqual(extraction.gate.n_censored, 1)
        self.assertFalse(extraction.gate.sufficient)
        self.assertEqual(
            extraction.observations[0].covariates["placement_best_arm"], "east_near"
        )
        self.assertEqual(extraction.observations[0].covariates["coal_safety_stock"], 2)


class SampleGateTests(unittest.TestCase):
    def test_four_real_generations_do_not_clear_the_gate(self) -> None:
        observations = [
            Observation(time_s=680.4, event=True, cause="fuel_and_power_starvation"),
            Observation(time_s=620.4, event=True, cause="fuel_and_power_starvation"),
            Observation(time_s=680.4, event=True, cause="fuel_and_power_starvation"),
            Observation(time_s=680.4, event=True, cause="fuel_and_power_starvation"),
        ]
        curve = kaplan_meier(observations)
        self.assertAlmostEqual(curve.survival_at(620.4), 0.75, places=12)
        self.assertAlmostEqual(curve.survival_at(680.4), 0.0, places=12)
        self.assertEqual(curve.median_survival_s, 680.4)
        self.assertEqual(curve.gate.n, 4)
        self.assertEqual(curve.gate.n_events, 4)
        self.assertEqual(curve.gate.n_censored, 0)
        self.assertEqual(curve.gate.verdict, VERDICT_INSUFFICIENT)
        self.assertFalse(curve.gate.sufficient)
        self.assertIn("4", curve.gate.reason)

    def test_every_result_carries_a_gate(self) -> None:
        observations = [
            Observation(time_s=1.0, event=True, cause="a", covariates={"x": 1.0}),
            Observation(time_s=2.0, event=True, cause="b", covariates={"x": 3.0}),
        ]
        for result in (
            kaplan_meier(observations),
            hazard_table(observations),
            competing_risks(observations),
            describe_by_stratum(observations, "x"),
        ):
            self.assertEqual(result.gate.n, 2)
            self.assertEqual(result.gate.n_events, 2)
            self.assertEqual(result.gate.n_censored, 0)
            self.assertFalse(result.gate.sufficient)


class StratumTests(unittest.TestCase):
    def _observations(self) -> list[Observation]:
        times = (100.0, 120.0, 140.0, 300.0, 320.0, 360.0)
        stocks = (2.0, 2.0, 3.0, 8.0, 9.0, 9.0)
        return [
            Observation(time_s=t, event=True, cause="fuel_starvation", covariates={"stock": s})
            for t, s in zip(times, stocks, strict=True)
        ]

    def test_numeric_covariate_splits_at_the_median(self) -> None:
        comparison = describe_by_stratum(self._observations(), "stock")
        self.assertEqual(comparison.covariate, "stock")
        self.assertEqual(len(comparison.strata), 2)
        self.assertEqual([s.n for s in comparison.strata], [3, 3])
        self.assertAlmostEqual(comparison.threshold, 5.5, places=12)

    def test_median_confidence_interval_declares_achieved_coverage(self) -> None:
        comparison = describe_by_stratum(self._observations(), "stock", confidence=0.95)
        merged = describe_by_stratum(
            [Observation(time_s=o.time_s, event=True, cause="c") for o in self._observations()],
            covariate=None,
        )
        stratum = merged.strata[0]
        self.assertEqual(stratum.n, 6)
        assert stratum.median_ci is not None
        # n = 6, order statistics [X_(1), X_(6)]: coverage = 1 - 2 * C(6,0)/2**6.
        self.assertAlmostEqual(stratum.median_ci.achieved_coverage, 1 - 2 / 64, places=12)
        self.assertTrue(stratum.median_ci.meets_nominal_level)
        self.assertEqual(stratum.median_ci.low, 100.0)
        self.assertEqual(stratum.median_ci.high, 360.0)
        self.assertEqual(comparison.gate.n, 6)

    def test_four_observations_cannot_reach_the_nominal_level(self) -> None:
        observations = [
            Observation(time_s=t, event=True, cause="c") for t in (620.4, 680.4, 680.4, 680.4)
        ]
        summary = describe_by_stratum(observations, covariate=None).strata[0]
        assert summary.median_ci is not None
        # n = 4, order statistics [X_(1), X_(4)]: coverage = 1 - 2 * C(4,0)/2**4.
        self.assertAlmostEqual(summary.median_ci.achieved_coverage, 0.875, places=12)
        self.assertFalse(summary.median_ci.meets_nominal_level)

    def test_censored_stratum_refuses_the_order_statistic_interval(self) -> None:
        observations = [
            Observation(time_s=100.0, event=True, cause="c"),
            Observation(time_s=200.0, event=False),
            Observation(time_s=300.0, event=True, cause="c"),
        ]
        summary = describe_by_stratum(observations, covariate=None).strata[0]
        self.assertIsNone(summary.median_ci)
        self.assertIn("censor", summary.median_ci_refusal)

    def test_comparison_refuses_inference_and_states_the_required_sample(self) -> None:
        comparison = describe_by_stratum(self._observations(), "stock")
        self.assertIsNone(comparison.inference)
        self.assertNotEqual(comparison.inference_refusal, "")
        self.assertIsNotNone(comparison.required_for_inference)
        assert comparison.required_for_inference is not None
        self.assertGreater(comparison.required_for_inference.required_events, comparison.gate.n)

    def test_categorical_covariate_groups_by_value(self) -> None:
        observations = [
            Observation(time_s=10.0, event=True, cause="c", covariates={"arm": "east_near"}),
            Observation(time_s=20.0, event=True, cause="c", covariates={"arm": "west_near"}),
            Observation(time_s=30.0, event=True, cause="c", covariates={"arm": "east_near"}),
        ]
        comparison = describe_by_stratum(observations, "arm")
        names = sorted(s.name for s in comparison.strata)
        self.assertEqual(names, ["arm=east_near", "arm=west_near"])
        self.assertIsNone(comparison.threshold)

    def test_observations_missing_the_covariate_are_excluded_not_imputed(self) -> None:
        observations = [
            Observation(time_s=10.0, event=True, cause="c", covariates={"x": 1.0}),
            Observation(time_s=20.0, event=True, cause="c"),
        ]
        comparison = describe_by_stratum(observations, "x")
        self.assertEqual(comparison.excluded_missing_covariate, 1)
        self.assertEqual(sum(s.n for s in comparison.strata), 1)


class RequiredSampleTests(unittest.TestCase):
    def test_required_events_for_hazard_ratio_two(self) -> None:
        result = required_events_for_hazard_ratio(2.0, alpha=0.05, power=0.80)
        z_alpha = NormalDist().inv_cdf(1 - 0.05 / 2)
        z_beta = NormalDist().inv_cdf(0.80)
        expected = (z_alpha + z_beta) ** 2 / (0.5 * 0.5 * math.log(2.0) ** 2)
        self.assertEqual(result.required_events, math.ceil(expected))
        self.assertEqual(result.required_events, 66)
        self.assertIsNone(result.required_observations)

    def test_smaller_effects_need_more_events(self) -> None:
        big = required_events_for_hazard_ratio(3.0)
        small = required_events_for_hazard_ratio(1.2)
        self.assertLess(big.required_events, small.required_events)

    def test_unbalanced_allocation_needs_more_events(self) -> None:
        balanced = required_events_for_hazard_ratio(2.0, allocation=0.5)
        skewed = required_events_for_hazard_ratio(2.0, allocation=0.2)
        self.assertLess(balanced.required_events, skewed.required_events)

    def test_event_probability_converts_events_into_observations(self) -> None:
        result = required_events_for_hazard_ratio(2.0, event_probability=0.5)
        self.assertEqual(result.required_observations, 2 * result.required_events)

    def test_null_or_impossible_effect_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            required_events_for_hazard_ratio(1.0)
        with self.assertRaises(ValueError):
            required_events_for_hazard_ratio(0.0)
        with self.assertRaises(ValueError):
            required_events_for_hazard_ratio(2.0, power=1.0)
        with self.assertRaises(ValueError):
            required_events_for_hazard_ratio(2.0, event_probability=0.0)


class ObservationValidationTests(unittest.TestCase):
    def test_negative_or_non_finite_time_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            Observation(time_s=-1.0, event=True, cause="c")
        with self.assertRaises(ValueError):
            Observation(time_s=float("nan"), event=False)
        with self.assertRaises(ValueError):
            Observation(time_s=float("inf"), event=True, cause="c")


if __name__ == "__main__":
    unittest.main()
