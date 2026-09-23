import unittest

from factorio_ai_lab.learning import autonomy as autonomy_module
from factorio_ai_lab.learning import factory_graph as factory_graph_module
from factorio_ai_lab.learning.autonomy import evaluate_factory_autonomy
from factorio_ai_lab.learning.factory_graph import (
    HALT_CAUSE_FUEL,
    HALT_CAUSE_FUEL_AND_POWER,
    HALT_CAUSE_NO_FACTORY,
    HALT_CAUSE_NONE_OBSERVED,
    POWER_STARVED_STATUSES,
    build_factory_graph,
)

LIVE_RATES = {"iron-plate": 1.0, "copper-plate": 1.0, "coal": 1.0}


def entity(name, x, y, status="working"):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "status": status,
    }


def closed_loop_rows(status="working"):
    """A factory that satisfies every physical gate of the closed-loop score."""
    layout = [
        ("electric-mining-drill", 0, 0),
        ("transport-belt", 2, 0),
        ("transport-belt", 3, 0),
        ("inserter", 4, 0),
        ("boiler", 5, 0),
        ("offshore-pump", 5, -4),
        ("steam-engine", 8, 0),
        ("small-electric-pole", 7, 2),
        ("lab", 9, 2),
        ("stone-furnace", 3, 6),
        ("transport-belt", 1, 6),
        ("inserter", 2, 6),
        ("inserter", 3, 5),
        ("inserter", 3, 7),
        ("wooden-chest", 3, 8),
        ("stone-furnace", 12, 6),
        ("transport-belt", 10, 6),
        ("inserter", 11, 6),
        ("inserter", 12, 5),
        ("inserter", 12, 7),
        ("wooden-chest", 12, 8),
    ]
    if status is None:
        return [
            {"name": name, "position": {"x": x, "y": y}}
            for name, x, y in layout
        ]
    return [entity(name, x, y, status) for name, x, y in layout]


class AutonomyMeasurementTests(unittest.TestCase):
    def test_measured_zero_intervention_still_closes_the_loop(self) -> None:
        report = evaluate_factory_autonomy(
            entities=closed_loop_rows(),
            interventions={},
            production_rates_per_s=LIVE_RATES,
            soak_runtime_s=120,
        )

        self.assertIs(report.closed_loop, True)
        self.assertEqual(report.score, 1.0)
        self.assertEqual(report.unmeasured_gates, ())

    def test_unmeasured_soak_is_not_a_zero_soak(self) -> None:
        report = evaluate_factory_autonomy(
            entities=closed_loop_rows(),
            interventions={},
            production_rates_per_s=LIVE_RATES,
            soak_runtime_s=None,
        )

        self.assertIsNone(report.soak_runtime_s)
        self.assertIsNone(report.topology["soak_long_enough"])
        self.assertIsNone(report.closed_loop)
        self.assertEqual(
            set(report.unmeasured_gates),
            {"soak_long_enough", "zero_manual_logistics", "producing_material"},
        )
        self.assertEqual(report.score, 0.625)

    def test_missing_intervention_accounting_is_not_zero_manual_work(self) -> None:
        report = evaluate_factory_autonomy(
            entities=closed_loop_rows(),
            interventions=None,
            production_rates_per_s=LIVE_RATES,
            soak_runtime_s=120,
        )

        self.assertIsNone(report.manual_logistics_calls)
        self.assertIsNone(report.topology["zero_manual_logistics"])
        self.assertIsNone(report.closed_loop)
        self.assertIn("zero_manual_logistics", report.unmeasured_gates)

    def test_energy_health_is_unmeasured_when_no_entity_reports_status(self) -> None:
        report = evaluate_factory_autonomy(
            entities=closed_loop_rows(status=None),
            interventions={},
            production_rates_per_s=LIVE_RATES,
            soak_runtime_s=120,
        )

        self.assertIsNone(report.topology["healthy_fuel"])
        self.assertIsNone(report.topology["healthy_power"])
        self.assertIsNone(report.closed_loop)
        self.assertIn("healthy_fuel", report.unmeasured_gates)
        self.assertIn("healthy_power", report.unmeasured_gates)

    def test_definite_failure_outranks_an_unmeasured_gate(self) -> None:
        report = evaluate_factory_autonomy(
            entities=[entity("burner-mining-drill", 0, 0, "no_fuel")],
            interventions=None,
            production_rates_per_s={},
            soak_runtime_s=None,
        )

        self.assertIs(report.closed_loop, False)
        self.assertEqual(report.score, 0.0)

    def test_quoted_status_is_normalized_like_the_graph(self) -> None:
        report = evaluate_factory_autonomy(
            entities=[entity("boiler", 0, 0, '"no_fuel"')],
            interventions={},
            production_rates_per_s={},
            soak_runtime_s=120,
        )

        self.assertEqual(report.no_fuel_entities, 1)
        self.assertIs(report.topology["healthy_fuel"], False)

    def test_to_dict_exposes_the_gates_that_were_not_measured(self) -> None:
        payload = evaluate_factory_autonomy(
            entities=closed_loop_rows(),
            interventions={},
            production_rates_per_s=LIVE_RATES,
            soak_runtime_s=None,
        ).to_dict()

        self.assertIsNone(payload["closed_loop"])
        self.assertIn("soak_long_enough", payload["unmeasured_gates"])
        self.assertIsNone(payload["soak_runtime_s"])


class StatusVocabularyTests(unittest.TestCase):
    def test_not_connected_is_not_a_factorio_status(self) -> None:
        self.assertNotIn("not_connected", POWER_STARVED_STATUSES)

        report = evaluate_factory_autonomy(
            entities=[entity("lab", 0, 0, "not_connected")],
            interventions={},
            production_rates_per_s={},
            soak_runtime_s=120,
        )
        self.assertEqual(report.no_power_entities, 0)

        graph = build_factory_graph([entity("lab", 0, 0, "not_connected")])
        self.assertEqual(graph["metrics"]["power_starved_entities"], 0)

    def test_both_modules_share_one_status_vocabulary(self) -> None:
        self.assertIs(
            autonomy_module.POWER_STARVED_STATUSES,
            factory_graph_module.POWER_STARVED_STATUSES,
        )
        self.assertIs(
            autonomy_module.FUEL_STARVED_STATUSES,
            factory_graph_module.FUEL_STARVED_STATUSES,
        )

    def test_real_disconnect_status_still_counts_as_power_failure(self) -> None:
        report = evaluate_factory_autonomy(
            entities=[
                entity("lab", 0, 0, "not_plugged_in_electric_network"),
                entity("electric-mining-drill", 2, 0, "no_power"),
            ],
            interventions={},
            production_rates_per_s={},
            soak_runtime_s=120,
        )

        self.assertEqual(report.no_power_entities, 2)
        self.assertIs(report.topology["healthy_power"], False)


class HaltCauseTests(unittest.TestCase):
    def test_fuel_starvation_is_named(self) -> None:
        graph = build_factory_graph(
            [
                entity("burner-mining-drill", 0, 0, "no_fuel"),
                entity("wooden-chest", 0, 2),
            ]
        )

        self.assertEqual(graph["metrics"]["halt_cause"], HALT_CAUSE_FUEL)
        self.assertIs(graph["metrics"]["entity_status_observed"], True)

    def test_fuel_and_power_starvation_are_reported_together(self) -> None:
        graph = build_factory_graph(
            [
                entity("burner-mining-drill", 0, 0, "no_fuel"),
                entity("electric-mining-drill", 4, 0, "no_power"),
            ]
        )

        self.assertEqual(
            graph["metrics"]["halt_cause"],
            HALT_CAUSE_FUEL_AND_POWER,
        )

    def test_running_factory_reports_no_observed_halt(self) -> None:
        graph = build_factory_graph(
            [
                entity("burner-mining-drill", 0, 0),
                entity("stone-furnace", 4, 0),
            ]
        )

        self.assertEqual(graph["metrics"]["halt_cause"], HALT_CAUSE_NONE_OBSERVED)

    def test_halt_cause_is_unmeasured_without_any_status(self) -> None:
        graph = build_factory_graph(
            [
                {"name": "burner-mining-drill", "position": {"x": 0, "y": 0}},
                {"name": "stone-furnace", "position": {"x": 4, "y": 0}},
            ]
        )

        self.assertIsNone(graph["metrics"]["halt_cause"])
        self.assertIs(graph["metrics"]["entity_status_observed"], False)
        self.assertEqual(graph["metrics"]["fuel_starved_entities"], 0)

    def test_empty_world_never_had_a_factory(self) -> None:
        graph = build_factory_graph([])

        self.assertEqual(graph["metrics"]["halt_cause"], HALT_CAUSE_NO_FACTORY)


if __name__ == "__main__":
    unittest.main()
