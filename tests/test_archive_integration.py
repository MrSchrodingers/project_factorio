"""
The selection loop has to use the niche archive, not merely be able to.

Two joints are covered here. After a generation is judged, its challenger is
offered to the archive and whatever came back -- placement or refusal -- is
recorded where a reader can audit it. Before the next generation mutates, the
parent is drawn from the archive instead of always being the global champion,
from a stream seeded by the run seed and the generation so the draw repeats.

The refusal path is a first-class case rather than an edge one: 32 of the 38
recorded generations carry no rate provenance and cannot be banded at all, so
an integration that only worked for bandable challengers would be untested
against the data the loop actually produces.
"""

from __future__ import annotations

import ast
import json
import pathlib
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from random import Random
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.learning.archive import (
    INSERTION_FOUNDED,
    INSERTION_REFUSED,
    REFUSAL_NO_MEASURED_PRODUCTION,
    NicheArchive,
)
from factorio_ai_lab.learning.survival import (
    RATE_SOURCE_ENDOGENOUS,
    RATE_SOURCE_INTERVENTION,
    FitnessVector,
)

RUNNER = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "factorio_ai_lab"
    / "experiments"
    / "curriculum_runner.py"
)

RUNNER_SOURCE = RUNNER.read_text(encoding="utf-8")

#: Rates whose provenance is classified, so the challenger can be banded.
MEASURED_METRICS = {
    "baseline_iron_rate_per_s": 0.25,
    "direct_smelting_plate_rate_per_s": 0.05,
}


@contextmanager
def sandboxed_runs(root: pathlib.Path) -> Iterator[None]:
    """Point every file the runner writes at a temporary directory."""
    with patch.multiple(
        curriculum_runner,
        RESEARCH_STATE=root / "research_state.json",
        ACTIVE_RUN=root / "active_run.json",
        RESEARCH_HISTORY=root / "research",
        EVOLUTION_CHAMPION=root / "evolution_champion.json",
        EVOLUTION_HISTORY=root / "evolution_history.jsonl",
        GENERATION_REPORTS=root / "generation_reports",
        KNOWLEDGE_LOG=root / "knowledge.jsonl",
        NICHE_ARCHIVE=root / "niche_archive.json",
    ):
        yield


def bandable_fitness(
    *,
    endogenous: float,
    intervention: float,
    capabilities: frozenset[str] = frozenset({"iron_backbone"}),
) -> FitnessVector:
    """A fitness the descriptor can place, because every rate has a source."""
    return FitnessVector(
        capabilities=capabilities,
        rates_per_s={"iron-ore": endogenous, "iron-plate": intervention},
        rate_sources={
            "iron-ore": RATE_SOURCE_ENDOGENOUS,
            "iron-plate": RATE_SOURCE_INTERVENTION,
        },
    )


def populated_archive() -> NicheArchive:
    """Four elites in four autonomy bands, each breedable from its record."""
    archive = NicheArchive()
    for index, share in enumerate((0.1, 0.4, 0.6, 0.9)):
        outcome = archive.insert(
            bandable_fitness(endogenous=share, intervention=1.0 - share),
            record={
                "run_id": f"run-{index}",
                "generation": index + 1,
                "configuration": {"routing_turn_penalty": 0.1 * (index + 1)},
            },
        )
        assert outcome.status == INSERTION_FOUNDED, outcome.status
    assert len(archive) == 4
    return archive


def last_report(root: pathlib.Path) -> dict[str, Any]:
    """The generation report the runner just appended."""
    lines = [
        line
        for line in (root / "evolution_history.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    return json.loads(lines[-1])


def judged_generation(
    root: pathlib.Path,
    *,
    metrics: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one selection inside the sandbox; answer (evolution, report)."""
    journal = curriculum_runner.ResearchJournal("curriculum-test")
    journal.state["metrics"] = dict(metrics)
    # With no incumbent on disk, compare_challenger rejects any challenger
    # that still carries an external input, and the default journal ships
    # bootstrap coal as one. Retiring it is what makes the first generation
    # promotable, which is the path that writes evolution_champion.json.
    journal.state["resource_accounting"] = {
        "exogenous_inputs": {"coal": {"status": "retired"}},
    }
    journal.state["evolution"]["challenger"]["configuration"] = {
        "routing_turn_penalty": 0.25,
    }
    curriculum_runner.finalize_evolution_selection(
        journal,
        achieved={"iron_backbone"},
        physical_graph=None,
    )
    return journal.state["evolution"], last_report(root)


class ArchiveInsertionTests(unittest.TestCase):
    def test_challenger_is_inserted_into_the_archive(self) -> None:
        with TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            with sandboxed_runs(root):
                evolution, report = judged_generation(root, metrics=MEASURED_METRICS)
                stored = NicheArchive.load(root / "niche_archive.json")
        self.assertEqual(len(stored), 1, "o desafiante nao entrou no arquivo")
        self.assertEqual(evolution["archive"]["status"], INSERTION_FOUNDED)
        self.assertEqual(report["archive"]["status"], INSERTION_FOUNDED)
        self.assertIsNotNone(report["archive"]["descriptor"])
        elite = stored.entries()[0]
        self.assertEqual(
            elite.record.get("configuration", {}).get("routing_turn_penalty"),
            0.25,
            "a elite guardada nao carrega a configuracao que a gerou",
        )

    def test_refusal_is_recorded_and_counted(self) -> None:
        with TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            with sandboxed_runs(root):
                evolution, report = judged_generation(root, metrics={})
                stored = NicheArchive.load(root / "niche_archive.json")
        self.assertEqual(evolution["archive"]["status"], INSERTION_REFUSED)
        self.assertEqual(
            evolution["archive"]["refusal"]["reason"],
            REFUSAL_NO_MEASURED_PRODUCTION,
        )
        self.assertEqual(
            report["archive"]["refusal_counts"],
            {REFUSAL_NO_MEASURED_PRODUCTION: 1},
            "a recusa nao foi contada no relatorio da geracao",
        )
        self.assertEqual(len(stored), 0, "uma recusa inventou um nicho")
        self.assertEqual(len(stored.refusals()), 1)

    def test_promotion_still_writes_the_global_champion(self) -> None:
        with TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            with sandboxed_runs(root):
                evolution, report = judged_generation(root, metrics=MEASURED_METRICS)
                champion = json.loads(
                    (root / "evolution_champion.json").read_text(encoding="utf-8")
                )
        self.assertTrue(report["decision"]["promoted"])
        self.assertEqual(champion["run_id"], "curriculum-test")
        self.assertEqual(evolution["champion"]["run_id"], "curriculum-test")

    def test_unreadable_archive_neither_stops_nor_is_overwritten(self) -> None:
        payload = '{"schema_version": "niche_archive_v0"}\n'
        with TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            (root / "niche_archive.json").write_text(payload, encoding="utf-8")
            with sandboxed_runs(root):
                evolution, report = judged_generation(root, metrics=MEASURED_METRICS)
                after = (root / "niche_archive.json").read_text(encoding="utf-8")
        self.assertEqual(
            after,
            payload,
            "um arquivo ilegivel foi sobrescrito, o que apaga as elites nele",
        )
        self.assertEqual(evolution["archive"]["archive_status"], "unreadable")
        self.assertTrue(report["archive"]["archive_detail"])
        self.assertTrue(report["decision"]["promoted"])

    def test_report_names_the_origin_of_the_parent(self) -> None:
        with TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            with sandboxed_runs(root):
                journal = curriculum_runner.ResearchJournal("curriculum-test")
                journal.state["metrics"] = dict(MEASURED_METRICS)
                journal.state["evolution"]["parent"] = {
                    "source": curriculum_runner.PARENT_SOURCE_ARCHIVE,
                    "run_id": "run-2",
                }
                curriculum_runner.finalize_evolution_selection(
                    journal,
                    achieved={"iron_backbone"},
                    physical_graph=None,
                )
                report = last_report(root)
        self.assertEqual(
            report["parent"]["source"],
            curriculum_runner.PARENT_SOURCE_ARCHIVE,
            "o relatorio nao diz de onde veio o pai da geracao",
        )


#: The configuration the global champion carries: the fallback parent.
CHAMPION_CONFIGURATION = {
    "routing_turn_penalty": 0.99,
    "placement_exploration": 2.0,
}


class ParentSelectionTests(unittest.TestCase):
    def select(
        self,
        archive: NicheArchive | None,
        *,
        rng: Random,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return curriculum_runner.select_mutation_parent(
            champion_configuration=CHAMPION_CONFIGURATION,
            archive=archive,
            rng=rng,
        )

    def test_parent_comes_from_the_archive_when_an_elite_exists(self) -> None:
        configuration, provenance = self.select(
            populated_archive(),
            rng=curriculum_runner.parent_sampling_rng(generation=4, seed=20260921),
        )
        self.assertEqual(provenance["source"], curriculum_runner.PARENT_SOURCE_ARCHIVE)
        self.assertNotEqual(
            configuration,
            CHAMPION_CONFIGURATION,
            "o pai continuou sendo o campeao global",
        )
        self.assertIn("routing_turn_penalty", configuration)
        self.assertIsNotNone(provenance["descriptor"])
        self.assertEqual(provenance["niche_count"], 4)

    def test_empty_archive_falls_back_to_the_global_champion(self) -> None:
        configuration, provenance = self.select(
            NicheArchive(),
            rng=curriculum_runner.parent_sampling_rng(generation=1, seed=1),
        )
        self.assertEqual(configuration, CHAMPION_CONFIGURATION)
        self.assertEqual(provenance["source"], curriculum_runner.PARENT_SOURCE_CHAMPION)
        self.assertEqual(
            provenance["reason"],
            curriculum_runner.PARENT_FALLBACK_EMPTY_ARCHIVE,
        )

    def test_missing_archive_falls_back_to_the_global_champion(self) -> None:
        configuration, provenance = self.select(
            None,
            rng=curriculum_runner.parent_sampling_rng(generation=1, seed=1),
        )
        self.assertEqual(configuration, CHAMPION_CONFIGURATION)
        self.assertEqual(provenance["source"], curriculum_runner.PARENT_SOURCE_CHAMPION)
        self.assertEqual(
            provenance["reason"],
            curriculum_runner.PARENT_FALLBACK_NO_ARCHIVE,
        )

    def test_elite_without_configuration_falls_back_to_the_champion(self) -> None:
        archive = NicheArchive()
        archive.insert(
            bandable_fitness(endogenous=0.9, intervention=0.1),
            record={"run_id": "run-x", "generation": 3},
        )
        configuration, provenance = self.select(
            archive,
            rng=curriculum_runner.parent_sampling_rng(generation=2, seed=7),
        )
        self.assertEqual(configuration, CHAMPION_CONFIGURATION)
        self.assertEqual(provenance["source"], curriculum_runner.PARENT_SOURCE_CHAMPION)
        self.assertEqual(
            provenance["reason"],
            curriculum_runner.PARENT_FALLBACK_NO_CONFIGURATION,
        )
        self.assertIsNotNone(
            provenance["descriptor"],
            "a amostra recusada nao foi identificada no relatorio",
        )

    def test_the_draw_consumes_the_injected_stream(self) -> None:
        archive = populated_archive()
        rng = Random(20260923)
        before = rng.getstate()
        self.select(archive, rng=rng)
        self.assertNotEqual(
            before,
            rng.getstate(),
            "o sorteio nao consumiu o rng recebido, logo nao depende da seed",
        )

    def test_same_seed_and_generation_draw_the_same_parent(self) -> None:
        archive = populated_archive()
        for generation in range(1, 9):
            first = self.select(
                archive,
                rng=curriculum_runner.parent_sampling_rng(
                    generation=generation, seed=20260921
                ),
            )
            second = self.select(
                archive,
                rng=curriculum_runner.parent_sampling_rng(
                    generation=generation, seed=20260921
                ),
            )
            self.assertEqual(
                first,
                second,
                f"geracao {generation} nao reproduziu o pai com a mesma seed",
            )

    def test_different_seeds_can_draw_different_parents(self) -> None:
        archive = populated_archive()
        drawn = {
            json.dumps(
                self.select(
                    archive,
                    rng=curriculum_runner.parent_sampling_rng(generation=5, seed=seed),
                )[0],
                sort_keys=True,
            )
            for seed in range(32)
        }
        self.assertGreater(
            len(drawn),
            1,
            "toda seed sorteou o mesmo pai: o arquivo nao esta sendo explorado",
        )


def _run_curriculum() -> ast.FunctionDef:
    for node in ast.walk(ast.parse(RUNNER_SOURCE)):
        if isinstance(node, ast.FunctionDef) and node.name == "run_curriculum":
            return node
    raise AssertionError("run_curriculum nao existe mais em curriculum_runner.py")


def _calls(function: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


class RunnerWiringTests(unittest.TestCase):
    """run_curriculum has to breed from the sampled parent, not beside it."""

    def setUp(self) -> None:
        self.function = _run_curriculum()
        selections = _calls(self.function, "select_mutation_parent")
        self.assertTrue(
            selections,
            "run_curriculum nao amostra o pai do arquivo de nichos",
        )
        self.selection = selections[0]
        assignments = [
            node
            for node in ast.walk(self.function)
            if isinstance(node, ast.Assign) and node.value is self.selection
        ]
        self.assertTrue(
            assignments,
            "o resultado de select_mutation_parent nao e atribuido",
        )
        target = assignments[0].targets[0]
        assert isinstance(target, ast.Tuple), (
            "select_mutation_parent deve responder configuracao e procedencia"
        )
        configuration, provenance = target.elts
        assert isinstance(configuration, ast.Name)
        assert isinstance(provenance, ast.Name)
        self.configuration_name = configuration.id
        self.provenance_name = provenance.id

    def test_the_mutation_departs_from_the_sampled_configuration(self) -> None:
        genomes = _calls(self.function, "challenger_genome")
        self.assertTrue(genomes, "run_curriculum nao monta mais um genoma")
        parent = _keyword(genomes[0], "champion_configuration")
        assert isinstance(parent, ast.Name), (
            "o genoma nao parte de uma configuracao nomeada pelo sorteio"
        )
        self.assertEqual(
            parent.id,
            self.configuration_name,
            "o genoma continua partindo do campeao global",
        )

    def test_the_draw_is_seeded_by_generation_and_run_seed(self) -> None:
        stream = _keyword(self.selection, "rng")
        assert isinstance(stream, ast.Call) and isinstance(stream.func, ast.Name), (
            "o sorteio do pai nao recebe um rng construido na chamada"
        )
        self.assertEqual(stream.func.id, "parent_sampling_rng")
        self.assertEqual(
            {keyword.arg for keyword in stream.keywords},
            {"generation", "seed"},
            "o rng do sorteio nao e semeado pela geracao e pela seed do run",
        )

    def test_the_origin_of_the_parent_is_recorded(self) -> None:
        recorded = [
            node
            for node in ast.walk(self.function)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "evolution"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "parent"
                for target in node.targets
            )
        ]
        self.assertTrue(
            recorded,
            'run_curriculum nao grava a origem do pai em evolution["parent"]',
        )
        names = {
            node.id
            for node in ast.walk(recorded[0].value)
            if isinstance(node, ast.Name)
        }
        self.assertIn(self.provenance_name, names)


if __name__ == "__main__":
    unittest.main()
