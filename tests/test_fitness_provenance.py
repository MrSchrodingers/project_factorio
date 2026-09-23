import unittest

from factorio_ai_lab.learning.factory_graph import HALT_CAUSE_FUEL, build_factory_graph
from factorio_ai_lab.learning.survival import (
    PRODUCTIVE_RUNTIME_STAGE_WINDOWS,
    RATE_PROTOCOL_SLEEP_LITERAL,
    RATE_SOURCE_ENDOGENOUS,
    RATE_SOURCE_INTERVENTION,
    FitnessVector,
    fitness_from_research,
)

LIVE_LAYOUT = [
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


def entity(name, x, y, status="working"):
    return {"name": name, "position": {"x": x, "y": y}, "status": status}


def closed_loop_rows():
    """A factory that satisfies every physical gate of the closed-loop score."""
    return [entity(name, x, y) for name, x, y in LIVE_LAYOUT]


class RateProvenanceTests(unittest.TestCase):
    def test_hand_fed_batches_do_not_count_as_automation(self) -> None:
        fitness = fitness_from_research(
            metrics={
                "belt_smelting_plate_rate_per_s": 2.0,
                "survival_coal_rate_per_s": 0.5,
            },
            achieved={"iron_backbone"},
        )

        self.assertEqual(
            fitness.rate_sources["iron-plate"],
            RATE_SOURCE_ENDOGENOUS,
        )
        self.assertEqual(
            fitness.rate_sources["coal"],
            RATE_SOURCE_INTERVENTION,
        )
        self.assertEqual(fitness.endogenous_rate_per_s, 2.0)
        self.assertEqual(fitness.intervention_rate_per_s, 0.5)
        self.assertEqual(fitness.total_rate_per_s, 2.5)

    def test_hand_fed_furnace_is_not_endogenous_flow(self) -> None:
        fitness = fitness_from_research(
            metrics={"direct_smelting_plate_rate_per_s": 1.4583333333333333},
            achieved=set(),
        )

        self.assertEqual(
            fitness.rate_sources["iron-plate"],
            RATE_SOURCE_INTERVENTION,
        )
        self.assertEqual(fitness.endogenous_rate_per_s, 0.0)
        self.assertEqual(
            fitness.intervention_rate_per_s,
            1.4583333333333333,
        )

    def test_unclassified_rate_refuses_a_partial_sum(self) -> None:
        vector = FitnessVector(
            rates_per_s={"iron-plate": 1.0, "mystery-item": 2.0},
            rate_sources={"iron-plate": RATE_SOURCE_ENDOGENOUS},
        )

        self.assertEqual(vector.unclassified_rate_keys, ("mystery-item",))
        self.assertIsNone(vector.endogenous_rate_per_s)
        self.assertIsNone(vector.intervention_rate_per_s)
        self.assertEqual(vector.total_rate_per_s, 3.0)

    def test_no_measured_rate_is_a_measured_zero(self) -> None:
        vector = FitnessVector()

        self.assertEqual(vector.unclassified_rate_keys, ())
        self.assertEqual(vector.endogenous_rate_per_s, 0.0)
        self.assertEqual(vector.intervention_rate_per_s, 0.0)

    def test_legacy_fitness_without_provenance_stays_readable(self) -> None:
        vector = FitnessVector.from_dict(
            {
                "capabilities": ["iron_backbone"],
                "rates_per_s": {"iron-plate": 1.5},
                "external_dependencies": 0,
                "failures": 0,
            }
        )

        self.assertEqual(vector.measurement_protocol, RATE_PROTOCOL_SLEEP_LITERAL)
        self.assertEqual(dict(vector.rate_sources), {})
        self.assertEqual(vector.total_rate_per_s, 1.5)
        self.assertIsNone(vector.endogenous_rate_per_s)
        self.assertIsNone(vector.productive_runtime_s)
        self.assertIsNone(vector.productive_runtime_source)
        self.assertIsNone(vector.halt_cause)

    def test_provenance_survives_a_round_trip(self) -> None:
        vector = FitnessVector(
            rates_per_s={"iron-plate": 2.0},
            rate_sources={"iron-plate": RATE_SOURCE_ENDOGENOUS},
            productive_runtime_s=320.4,
            productive_runtime_source=PRODUCTIVE_RUNTIME_STAGE_WINDOWS,
            halt_cause=HALT_CAUSE_FUEL,
        )

        restored = FitnessVector.from_dict(vector.to_dict())

        self.assertEqual(restored, vector)
        self.assertEqual(vector.to_dict()["endogenous_rate_per_s"], 2.0)


class MortalityEvidenceTests(unittest.TestCase):
    def test_productive_runtime_sums_only_windows_with_output(self) -> None:
        fitness = fitness_from_research(
            metrics={
                "coal_mining_duration_s": 54.0,
                "coal_output": 39.0,
                "copper_smelting_duration_s": 109.3,
                "copper_plate_output": 0.0,
            },
            achieved=set(),
        )

        self.assertEqual(fitness.productive_runtime_s, 54.0)
        self.assertEqual(
            fitness.productive_runtime_source,
            PRODUCTIVE_RUNTIME_STAGE_WINDOWS,
        )

    def test_productive_runtime_is_none_when_no_window_was_measured(self) -> None:
        fitness = fitness_from_research(metrics={}, achieved=set())

        self.assertIsNone(fitness.productive_runtime_s)
        self.assertIsNone(fitness.productive_runtime_source)

    def test_halt_cause_travels_from_the_graph_into_the_fitness(self) -> None:
        graph = build_factory_graph(
            [
                entity("burner-mining-drill", 0, 0, "no_fuel"),
                entity("wooden-chest", 0, 2),
            ]
        )

        fitness = fitness_from_research(
            metrics={},
            achieved=set(),
            physical_graph=graph,
        )

        self.assertEqual(fitness.halt_cause, HALT_CAUSE_FUEL)

    def test_halt_cause_is_none_without_a_graph(self) -> None:
        fitness = fitness_from_research(metrics={}, achieved=set())

        self.assertIsNone(fitness.halt_cause)


class AutonomyFromTerminalSnapshotTests(unittest.TestCase):
    def test_autonomy_is_measured_from_the_snapshot_when_no_soak_ran(self) -> None:
        graph = build_factory_graph(closed_loop_rows())

        fitness = fitness_from_research(
            metrics={
                "belt_smelting_plate_rate_per_s": 2.0,
                "interventions": {
                    "post_bootstrap_committed": {
                        "manual_harvest_calls": 0,
                        "manual_transfer_calls": 34,
                        "manual_craft_calls": 0,
                    }
                },
            },
            achieved={"iron_backbone"},
            physical_graph=graph,
        )

        self.assertAlmostEqual(fitness.autonomy_score, 0.625)
        self.assertIs(fitness.closed_loop_autonomy, False)
        self.assertEqual(fitness.manual_logistics_calls, 34)

    def test_autonomy_stays_unmeasured_without_a_snapshot(self) -> None:
        fitness = fitness_from_research(metrics={}, achieved=set())

        self.assertIsNone(fitness.autonomy_score)
        self.assertIsNone(fitness.closed_loop_autonomy)

    def test_soak_payload_still_wins_over_the_derived_snapshot(self) -> None:
        graph = build_factory_graph(closed_loop_rows())

        fitness = fitness_from_research(
            metrics={
                "autonomy": {
                    "score": 1.0,
                    "closed_loop": True,
                    "manual_logistics_calls": 0,
                },
            },
            achieved=set(),
            physical_graph=graph,
        )

        self.assertEqual(fitness.autonomy_score, 1.0)
        self.assertIs(fitness.closed_loop_autonomy, True)


if __name__ == "__main__":
    unittest.main()
