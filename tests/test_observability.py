import tempfile
import unittest
from pathlib import Path

from factorio_ai_lab.observability.store import ExperimentStore, RunMetadata


class ExperimentStoreTests(unittest.TestCase):
    def test_records_reproducible_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "experiments.sqlite"
            with ExperimentStore(database) as store:
                run_id = store.start_run(
                    RunMetadata(
                        environment="synthetic-routing-v1",
                        seed=7,
                        git_sha="abc1234",
                        model=None,
                        config={"turn_penalty": 0.25},
                    )
                )
                store.log_metric(run_id, 0, "route_length", 42)
                store.log_metric(run_id, 0, "turns", 5)
                store.log_event(run_id, 0, "planner_finished", {"expanded": 120})
                store.finish_run(run_id)

                self.assertEqual(
                    store.metrics_for_run(run_id),
                    [(0, "route_length", 42.0), (0, "turns", 5.0)],
                )


if __name__ == "__main__":
    unittest.main()
