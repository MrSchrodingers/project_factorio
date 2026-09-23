"""
Tests for the behavioural niche archive.

The real-data test reads runs/evolution_history.jsonl and rebuilds every
challenger with FitnessVector.from_dict, so the refusal rule is exercised
against the provenance that the recorded generations actually carry rather
than against a fixture written to pass.
"""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path
from typing import Any

from factorio_ai_lab.learning.archive import (
    ARCHIVE_SCHEMA_VERSION,
    INSERTION_FOUNDED,
    INSERTION_KEPT,
    INSERTION_REFUSED,
    INSERTION_REPLACED,
    REFUSAL_NO_MEASURED_PRODUCTION,
    REFUSAL_UNCLASSIFIED_PROVENANCE,
    ArchiveSchemaError,
    BehaviorDescriptor,
    DescriptorRefusal,
    NicheArchive,
    behavior_descriptor,
)
from factorio_ai_lab.learning.survival import (
    RATE_SOURCE_ENDOGENOUS,
    RATE_SOURCE_INTERVENTION,
    FitnessVector,
)

HISTORY_PATH = (
    Path(__file__).resolve().parents[1] / "runs" / "evolution_history.jsonl"
)


def recorded_generations() -> list[dict[str, Any]]:
    """Every generation written by the evolution loop, newest last."""
    if not HISTORY_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def measured_fitness(
    *,
    endogenous: float,
    intervention: float,
    capabilities: frozenset[str] = frozenset({"iron_backbone"}),
    halt_cause: str | None = None,
    inherited: frozenset[str] | None = None,
    failures: int = 0,
    external_dependencies: int = 0,
) -> FitnessVector:
    """A fitness whose rate provenance is fully classified, so it is bandable."""
    return FitnessVector(
        capabilities=capabilities,
        inherited_capabilities=inherited,
        rates_per_s={"iron-plate": endogenous, "copper-plate": intervention},
        rate_sources={
            "iron-plate": RATE_SOURCE_ENDOGENOUS,
            "copper-plate": RATE_SOURCE_INTERVENTION,
        },
        halt_cause=halt_cause,
        failures=failures,
        external_dependencies=external_dependencies,
    )


class DescriptorTests(unittest.TestCase):
    def test_autonomy_band_follows_the_endogenous_share(self) -> None:
        mostly_manual = behavior_descriptor(
            measured_fitness(endogenous=0.2, intervention=1.8)
        )
        mostly_endogenous = behavior_descriptor(
            measured_fitness(endogenous=1.8, intervention=0.2)
        )
        self.assertIsInstance(mostly_manual, BehaviorDescriptor)
        self.assertIsInstance(mostly_endogenous, BehaviorDescriptor)
        self.assertNotEqual(
            mostly_manual.autonomy_band, mostly_endogenous.autonomy_band
        )

    def test_band_edges_are_closed_on_the_left(self) -> None:
        # 1.0 / (1.0 + 3.0) == 0.25 belongs to [0.25, 0.50), not to [0, 0.25).
        at_edge = behavior_descriptor(
            measured_fitness(endogenous=1.0, intervention=3.0)
        )
        below_edge = behavior_descriptor(
            measured_fitness(endogenous=1.0, intervention=4.0)
        )
        fully_endogenous = behavior_descriptor(
            measured_fitness(endogenous=1.0, intervention=0.0)
        )
        self.assertNotEqual(at_edge.autonomy_band, below_edge.autonomy_band)
        self.assertEqual(at_edge.autonomy_band, "autonomy_25_50")
        self.assertEqual(below_edge.autonomy_band, "autonomy_00_25")
        # The top band is closed on the right so 1.0 is not a band of its own.
        self.assertEqual(fully_endogenous.autonomy_band, "autonomy_75_100")

    def test_inherited_capabilities_are_not_counted_as_built(self) -> None:
        capabilities = frozenset(
            {"iron_backbone", "coal_mining", "copper_mining", "copper_smelting"}
        )
        built_everything = behavior_descriptor(
            measured_fitness(
                endogenous=1.0, intervention=1.0, capabilities=capabilities
            )
        )
        inherited_everything = behavior_descriptor(
            measured_fitness(
                endogenous=1.0,
                intervention=1.0,
                capabilities=capabilities,
                inherited=capabilities,
            )
        )
        self.assertEqual(built_everything.capability_band, "built_3_4")
        self.assertEqual(inherited_everything.capability_band, "built_0")

    def test_unmeasured_provenance_is_refused_not_banded(self) -> None:
        # Shape of the generations recorded before rate_sources existed.
        legacy = FitnessVector(
            capabilities=frozenset({"iron_backbone"}),
            rates_per_s={"iron-plate": 1.5},
        )
        self.assertIsNone(legacy.endogenous_rate_per_s)
        refusal = behavior_descriptor(legacy)
        self.assertIsInstance(refusal, DescriptorRefusal)
        self.assertEqual(refusal.reason, REFUSAL_UNCLASSIFIED_PROVENANCE)
        self.assertIn("iron-plate", refusal.detail)

    def test_no_measured_production_is_refused_not_read_as_fully_manual(self) -> None:
        nothing_produced = FitnessVector(capabilities=frozenset({"iron_backbone"}))
        self.assertEqual(nothing_produced.endogenous_rate_per_s, 0.0)
        refusal = behavior_descriptor(nothing_produced)
        self.assertIsInstance(refusal, DescriptorRefusal)
        self.assertEqual(refusal.reason, REFUSAL_NO_MEASURED_PRODUCTION)

    def test_absent_halt_cause_is_distinct_from_any_named_cause(self) -> None:
        unobserved = behavior_descriptor(
            measured_fitness(endogenous=1.0, intervention=1.0, halt_cause=None)
        )
        named = behavior_descriptor(
            measured_fitness(
                endogenous=1.0, intervention=1.0, halt_cause="no_factory"
            )
        )
        spelled_unknown = behavior_descriptor(
            measured_fitness(
                endogenous=1.0, intervention=1.0, halt_cause="unknown"
            )
        )
        self.assertIsNone(unobserved.halt_cause)
        self.assertNotEqual(unobserved, named)
        self.assertNotEqual(unobserved, spelled_unknown)


class InsertionTests(unittest.TestCase):
    def test_empty_niche_accepts_the_candidate(self) -> None:
        archive = NicheArchive()
        result = archive.insert(
            measured_fitness(endogenous=1.0, intervention=1.0),
            record={"run_id": "a"},
        )
        self.assertEqual(result.status, INSERTION_FOUNDED)
        self.assertTrue(result.accepted)
        self.assertEqual(len(archive), 1)
        self.assertIsNone(result.decision)

    def test_occupied_niche_keeps_the_elite_when_the_candidate_ties(self) -> None:
        archive = NicheArchive()
        elite = measured_fitness(endogenous=1.0, intervention=1.0)
        archive.insert(elite, record={"run_id": "elite"})
        result = archive.insert(
            measured_fitness(endogenous=1.0, intervention=1.0),
            record={"run_id": "twin"},
        )
        self.assertEqual(result.status, INSERTION_KEPT)
        self.assertFalse(result.accepted)
        self.assertIsNotNone(result.decision)
        self.assertFalse(result.decision.promoted)
        self.assertEqual(len(archive), 1)
        self.assertEqual(result.elite.record["run_id"], "elite")
        stats = archive.stats(result.descriptor)
        self.assertEqual((stats.contests, stats.replacements), (1, 0))

    def test_occupied_niche_keeps_the_elite_when_the_candidate_regresses(self) -> None:
        archive = NicheArchive()
        archive.insert(
            measured_fitness(endogenous=2.0, intervention=2.0),
            record={"run_id": "elite"},
        )
        result = archive.insert(
            measured_fitness(endogenous=1.0, intervention=1.0),
            record={"run_id": "worse"},
        )
        self.assertEqual(result.status, INSERTION_KEPT)
        self.assertTrue(result.decision.regressions)
        self.assertEqual(archive.elite(result.descriptor).record["run_id"], "elite")

    def test_occupied_niche_swaps_only_on_a_promoted_challenger(self) -> None:
        archive = NicheArchive()
        base = frozenset({"iron_backbone", "coal_mining", "copper_mining"})
        archive.insert(
            measured_fitness(endogenous=1.0, intervention=1.0, capabilities=base),
            record={"run_id": "elite"},
        )
        result = archive.insert(
            measured_fitness(
                endogenous=1.0,
                intervention=1.0,
                capabilities=base | {"assembler_gears"},
            ),
            record={"run_id": "better"},
        )
        self.assertEqual(result.status, INSERTION_REPLACED)
        self.assertTrue(result.accepted)
        self.assertTrue(result.decision.promoted)
        self.assertEqual(result.displaced.record["run_id"], "elite")
        self.assertEqual(len(archive), 1)
        stats = archive.stats(result.descriptor)
        self.assertEqual((stats.contests, stats.replacements), (1, 1))

    def test_a_different_halt_cause_opens_its_own_niche(self) -> None:
        archive = NicheArchive()
        archive.insert(
            measured_fitness(endogenous=1.0, intervention=1.0, halt_cause=None)
        )
        archive.insert(
            measured_fitness(
                endogenous=1.0, intervention=1.0, halt_cause="fuel_starvation"
            )
        )
        self.assertEqual(len(archive), 2)
        halt_causes = {entry.descriptor.halt_cause for entry in archive.entries()}
        self.assertEqual(halt_causes, {None, "fuel_starvation"})

    def test_a_refused_candidate_is_counted_and_never_enters(self) -> None:
        archive = NicheArchive()
        result = archive.insert(
            FitnessVector(
                capabilities=frozenset({"iron_backbone"}),
                rates_per_s={"iron-plate": 1.5},
            ),
            record={"run_id": "legacy", "generation": 6},
        )
        self.assertEqual(result.status, INSERTION_REFUSED)
        self.assertFalse(result.accepted)
        self.assertIsNone(result.descriptor)
        self.assertEqual(len(archive), 0)
        self.assertEqual(len(archive.refusals()), 1)
        self.assertEqual(
            archive.refusal_counts(), {REFUSAL_UNCLASSIFIED_PROVENANCE: 1}
        )
        self.assertEqual(archive.refusals()[0].record["run_id"], "legacy")


class SamplingTests(unittest.TestCase):
    def _populated(self) -> NicheArchive:
        archive = NicheArchive()
        for index, (endogenous, intervention, halt_cause) in enumerate(
            (
                (0.2, 1.8, None),
                (1.0, 1.0, "fuel_starvation"),
                (1.8, 0.2, "no_factory"),
            )
        ):
            archive.insert(
                measured_fitness(
                    endogenous=endogenous,
                    intervention=intervention,
                    halt_cause=halt_cause,
                ),
                record={"run_id": f"run-{index}"},
            )
        return archive

    @staticmethod
    def _draw(archive: NicheArchive, rng: random.Random, draws: int) -> list[str]:
        return [
            archive.sample_parent(rng).record["run_id"] for _ in range(draws)
        ]

    def test_empty_archive_samples_nothing(self) -> None:
        self.assertIsNone(NicheArchive().sample_parent(random.Random(1)))

    def test_sampling_is_reproducible_for_a_given_seed(self) -> None:
        archive = self._populated()
        self.assertEqual(len(archive), 3)
        first = self._draw(archive, random.Random(20260923), 40)
        second = self._draw(archive, random.Random(20260923), 40)
        self.assertEqual(first, second)

    def test_sampling_spreads_over_the_occupied_niches(self) -> None:
        archive = self._populated()
        rng = random.Random(7)
        drawn = {archive.sample_parent(rng).record["run_id"] for _ in range(60)}
        self.assertEqual(drawn, {"run-0", "run-1", "run-2"})

    def test_a_different_seed_gives_a_different_draw_order(self) -> None:
        archive = self._populated()
        self.assertNotEqual(
            self._draw(archive, random.Random(1), 40),
            self._draw(archive, random.Random(2), 40),
        )


class PersistenceTests(unittest.TestCase):
    def _archive_with_history(self) -> NicheArchive:
        archive = NicheArchive()
        base = frozenset({"iron_backbone", "coal_mining", "copper_mining"})
        archive.insert(
            measured_fitness(endogenous=1.0, intervention=1.0, capabilities=base),
            record={"run_id": "elite"},
        )
        archive.insert(
            measured_fitness(
                endogenous=1.0,
                intervention=1.0,
                capabilities=base | {"assembler_gears"},
            ),
            record={"run_id": "better"},
        )
        archive.insert(
            measured_fitness(
                endogenous=1.8, intervention=0.2, halt_cause="no_factory"
            ),
            record={"run_id": "other-niche"},
        )
        archive.insert(
            FitnessVector(
                capabilities=frozenset({"iron_backbone"}),
                rates_per_s={"iron-plate": 1.5},
            ),
            record={"run_id": "legacy"},
        )
        return archive

    def test_round_trip_preserves_niches_counters_and_refusals(self) -> None:
        archive = self._archive_with_history()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "archive.json"
            archive.save(path)
            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            restored = NicheArchive.load(path)

        self.assertEqual(len(restored), len(archive))
        self.assertEqual(restored.entries(), archive.entries())
        for entry in archive.entries():
            self.assertEqual(
                restored.stats(entry.descriptor), archive.stats(entry.descriptor)
            )
        self.assertEqual(restored.refusals(), archive.refusals())
        self.assertEqual(restored.refusal_counts(), archive.refusal_counts())

    def test_round_trip_keeps_an_absent_halt_cause_absent(self) -> None:
        archive = NicheArchive()
        archive.insert(
            measured_fitness(endogenous=1.0, intervention=1.0, halt_cause=None),
            record={"run_id": "unobserved"},
        )
        restored = NicheArchive.from_dict(json.loads(json.dumps(archive.to_dict())))
        self.assertEqual(len(restored), 1)
        self.assertIsNone(restored.entries()[0].descriptor.halt_cause)

    def test_missing_file_loads_an_empty_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = NicheArchive.load(Path(directory) / "absent.json")
        self.assertEqual(len(archive), 0)

    def test_an_unknown_schema_version_is_refused_explicitly(self) -> None:
        payload = self._archive_with_history().to_dict()
        payload["schema_version"] = "niche_archive_v999"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ArchiveSchemaError) as caught:
                NicheArchive.load(path)
        message = str(caught.exception)
        self.assertIn("niche_archive_v999", message)
        self.assertIn(ARCHIVE_SCHEMA_VERSION, message)

    def test_a_payload_without_a_schema_version_is_refused(self) -> None:
        with self.assertRaises(ArchiveSchemaError):
            NicheArchive.from_dict({"niches": [], "refusals": []})

    def test_unreadable_json_is_refused_instead_of_read_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ArchiveSchemaError):
                NicheArchive.load(path)


@unittest.skipUnless(
    HISTORY_PATH.exists(), f"{HISTORY_PATH} is not present in this checkout"
)
class RecordedHistoryTests(unittest.TestCase):
    """The archive fed with the generations this project actually ran."""

    records: list[dict[str, Any]] = recorded_generations()

    def _fill(self) -> NicheArchive:
        archive = NicheArchive()
        for row in self.records:
            challenger = row.get("challenger") or {}
            fitness = FitnessVector.from_dict(challenger.get("fitness") or {})
            archive.insert(
                fitness,
                record={
                    "run_id": challenger.get("run_id"),
                    "generation": row.get("generation"),
                },
            )
        return archive

    def test_generations_recorded_without_provenance_are_refused(self) -> None:
        archive = self._fill()
        measured = [
            row
            for row in self.records
            if (row.get("challenger") or {}).get("fitness", {}).get("rate_sources")
        ]
        self.assertTrue(measured, "history carries no rate_sources at all")
        self.assertEqual(
            len(archive.refusals()), len(self.records) - len(measured)
        )
        self.assertEqual(
            set(archive.refusal_counts()), {REFUSAL_UNCLASSIFIED_PROVENANCE}
        )

    def test_every_archived_elite_carries_measured_provenance(self) -> None:
        archive = self._fill()
        self.assertTrue(archive.entries())
        for entry in archive.entries():
            self.assertEqual(entry.fitness.unclassified_rate_keys, ())
            self.assertIsNotNone(entry.fitness.endogenous_rate_per_s)

    def test_the_recorded_history_round_trips(self) -> None:
        archive = self._fill()
        restored = NicheArchive.from_dict(json.loads(json.dumps(archive.to_dict())))
        self.assertEqual(restored.entries(), archive.entries())
        self.assertEqual(len(restored.refusals()), len(archive.refusals()))


if __name__ == "__main__":
    unittest.main()
