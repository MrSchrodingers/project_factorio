import unittest
from dataclasses import dataclass

from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    enforce_minimum_eval_timeout,
)


@dataclass(frozen=True)
class FakeAction:
    agent_idx: int
    code: str
    game_state: str | None


class FakeEnvironment:
    def __init__(self) -> None:
        self.state = 'initial'
        self.reset_calls: list[tuple[str | None, int | None]] = []

    def reset(self, *, options=None, seed=None):
        game_state = None if options is None else options.get('game_state')
        self.state = 'initial' if game_state is None else game_state
        self.reset_calls.append((game_state, seed))
        return {'state': self.state}

    def step(self, action):
        assert isinstance(action, FakeAction)
        self.last_action = action
        self.state = f'{self.state}|{action.code}'
        return (
            {'raw_text': action.code},
            1.0,
            False,
            False,
            {'output_game_state': self.state},
        )

    def close(self):
        pass


def fake_action_factory(agent_idx, code, game_state):
    return FakeAction(agent_idx, code, game_state)


class FakeInstance:
    def __init__(self) -> None:
        self.timeouts: list[int] = []

    def eval(self, expr, agent_idx=0, timeout=60):
        self.timeouts.append(int(timeout))
        return expr, agent_idx, timeout


class FakeWrappedEnvironment:
    def __init__(self) -> None:
        self.instance = FakeInstance()
        self.unwrapped = self


class TransactionalFLEExecutorTests(unittest.TestCase):
    def test_eval_timeout_floor_is_local_idempotent_and_monotonic(self) -> None:
        env = FakeWrappedEnvironment()

        applied = enforce_minimum_eval_timeout(
            env,
            minimum_seconds=300,
        )
        self.assertEqual(applied, 300)

        env.instance.eval("short", agent_idx=2, timeout=120)
        env.instance.eval("long", agent_idx=2, timeout=420)
        self.assertEqual(env.instance.timeouts, [300, 420])

        applied_again = enforce_minimum_eval_timeout(
            env,
            minimum_seconds=180,
        )
        self.assertEqual(applied_again, 300)
        env.instance.eval("again", timeout=60)
        self.assertEqual(env.instance.timeouts[-1], 300)

    def test_commits_accepted_state(self) -> None:
        env = FakeEnvironment()
        executor = TransactionalFLEExecutor(env, action_factory=fake_action_factory)
        executor.reset(seed=11)

        result = executor.execute('build', accept=lambda _: True)

        self.assertTrue(result.accepted)
        self.assertEqual(executor.game_state, 'initial|build')
        self.assertEqual(env.state, 'initial|build')


    def test_live_action_keeps_checkpoint_only_for_rollback(self) -> None:
        env = FakeEnvironment()
        executor = TransactionalFLEExecutor(env, action_factory=fake_action_factory)
        executor.game_state = "checkpoint"
        env.state = "checkpoint"

        result = executor.execute(
            "build()",
            accept=lambda _: True,
            use_checkpoint_for_action=False,
        )

        self.assertTrue(result.accepted)
        self.assertIsNone(env.last_action.game_state)
        self.assertEqual(result.checkpoint_before, "checkpoint")
        self.assertEqual(executor.game_state, "checkpoint|build()")

    def test_rolls_back_rejected_state(self) -> None:
        env = FakeEnvironment()
        executor = TransactionalFLEExecutor(env, action_factory=fake_action_factory)
        executor.reset()
        executor.execute('good', accept=lambda _: True)

        rejected = executor.execute('bad', accept=lambda _: False)

        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.checkpoint_before, 'initial|good')
        self.assertEqual(executor.game_state, 'initial|good')
        self.assertEqual(env.state, 'initial|good')

    def test_intervention_counters_respect_rollback(self) -> None:
        env = FakeEnvironment()
        executor = TransactionalFLEExecutor(
            env,
            action_factory=fake_action_factory,
        )
        executor.reset()

        executor.execute(
            """insert_item(Prototype.Coal, drill, quantity=2)
extract_item(Prototype.IronOre, chest, quantity=3)""",
            accept=lambda _: True,
        )
        executor.execute(
            "harvest_resource(pos, quantity=4)",
            accept=lambda _: False,
        )

        counters = executor.intervention_snapshot()
        self.assertEqual(
            counters["committed"]["manual_transfer_calls"],
            2,
        )
        self.assertEqual(
            counters["committed"]["manual_harvest_calls"],
            0,
        )
        self.assertEqual(
            counters["attempted"]["manual_harvest_calls"],
            1,
        )

    def test_next_action_uses_last_committed_checkpoint(self) -> None:
        env = FakeEnvironment()
        executor = TransactionalFLEExecutor(env, action_factory=fake_action_factory)
        executor.reset()
        executor.execute('good', accept=lambda _: True)
        executor.execute('bad', accept=lambda _: False)
        final = executor.execute('repair', accept=lambda _: True)

        self.assertEqual(final.checkpoint_before, 'initial|good')
        self.assertEqual(executor.game_state, 'initial|good|repair')


if __name__ == '__main__':
    unittest.main()
