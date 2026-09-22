import tempfile
import unittest
from pathlib import Path

from factorio_ai_lab.learning.model_arena import ModelArena, ModelCandidate


class ModelArenaTests(unittest.TestCase):
    def test_ineligible_model_cannot_be_champion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            arena = ModelArena(Path(tmp) / "registry.json")
            decision = arena.select(
                ModelCandidate(
                    task="world",
                    model_id="gru-a",
                    protocol="p1",
                    eligible=False,
                    primary_score=0.8,
                )
            )
            self.assertFalse(decision.promoted)

    def test_first_eligible_model_becomes_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            arena = ModelArena(Path(tmp) / "registry.json")
            decision = arena.select(
                ModelCandidate(
                    task="spatial",
                    model_id="mlp-a",
                    protocol="p1",
                    eligible=True,
                    primary_score=1.14,
                )
            )
            self.assertTrue(decision.promoted)

    def test_better_model_replaces_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            arena = ModelArena(Path(tmp) / "registry.json")
            arena.select(
                ModelCandidate(
                    task="spatial",
                    model_id="mlp-a",
                    protocol="p1",
                    eligible=True,
                    primary_score=1.14,
                    secondary_score=0.10,
                )
            )
            decision = arena.select(
                ModelCandidate(
                    task="spatial",
                    model_id="attention-b",
                    protocol="p1",
                    eligible=True,
                    primary_score=1.10,
                    secondary_score=0.10,
                )
            )
            self.assertTrue(decision.promoted)

    def test_secondary_regression_blocks_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            arena = ModelArena(Path(tmp) / "registry.json")
            arena.select(
                ModelCandidate(
                    task="world",
                    model_id="gru-a",
                    protocol="p1",
                    eligible=True,
                    primary_score=0.9,
                    secondary_score=0.10,
                )
            )
            decision = arena.select(
                ModelCandidate(
                    task="world",
                    model_id="gru-b",
                    protocol="p1",
                    eligible=True,
                    primary_score=0.8,
                    secondary_score=0.20,
                )
            )
            self.assertFalse(decision.promoted)


if __name__ == "__main__":
    unittest.main()
