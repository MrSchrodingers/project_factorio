import base64
import json
import unittest
import zlib
from dataclasses import dataclass

from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    enforce_minimum_eval_timeout,
    enforce_pathfinding_retry_floor,
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




def _encode_entities(rows):
    raw=json.dumps(rows).encode("utf-8")
    return base64.b64encode(zlib.compress(raw)).decode("ascii")


class FakeSnapshotNamespace:
    def __init__(self, rows) -> None:
        self.rows=[dict(row) for row in rows]
        self.loaded_batches=[]

    def _save_entity_state(self, compress=True, encode=True):
        assert compress is True
        assert encode is True
        return _encode_entities(self.rows)

    def _load_entity_state(self, rows, decompress=False):
        assert decompress is False
        batch=[dict(row) for row in rows]
        self.loaded_batches.append(batch)
        existing={
            (str(row["name"]),str(row["position"]["x"]),str(row["position"]["y"]))
            for row in self.rows
        }
        for row in batch:
            key=(str(row["name"]),str(row["position"]["x"]),str(row["position"]["y"]))
            if key not in existing:
                self.rows.append(row)
                existing.add(key)
        return True


class FakeSnapshotInstance:
    def __init__(self, namespace) -> None:
        self.first_namespace=namespace


class FakeRollbackEnvironment(FakeEnvironment):
    def __init__(self, checkpoint, *, lose_name: str) -> None:
        super().__init__()
        self.checkpoint=checkpoint
        self.lose_name=lose_name
        self.namespace=FakeSnapshotNamespace(
            [
                row for row in checkpoint.rows
                if str(row["name"]).replace('"',"") != lose_name
            ]
        )
        self.instance=FakeSnapshotInstance(self.namespace)
        self.unwrapped=self

    def reset(self, *, options=None, seed=None):
        game_state=None if options is None else options.get("game_state")
        self.reset_calls.append((game_state,seed))
        self.state='initial' if game_state is None else game_state
        if game_state is self.checkpoint:
            self.namespace.rows=[
                dict(row) for row in self.checkpoint.rows
                if str(row["name"]).replace('"',"") != self.lose_name
            ]
        return {'state':self.state}


class FakeCheckpoint:
    def __init__(self, rows) -> None:
        self.rows=[dict(row) for row in rows]
        self.entities=_encode_entities(self.rows)


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


class FakeGetPath:
    def __init__(self) -> None:
        self.attempts: list[int] = []

    def __call__(self, path_handle: int, max_attempts: int=10):
        self.attempts.append(int(max_attempts))
        return [path_handle]


class FakeMoveTool:
    def __init__(self) -> None:
        self.get_path=FakeGetPath()


class FakePathInstance:
    def __init__(self) -> None:
        tool=FakeMoveTool()

        def move_to(position):
            return position

        move_to.__wrapped__=tool
        move_to.get_path=tool.get_path
        namespace=type("FakeNamespace",(),{})()
        namespace.move_to=move_to
        self.namespaces=[namespace]


class FakePathEnvironment:
    def __init__(self) -> None:
        self.instance=FakePathInstance()
        self.unwrapped=self


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

    def test_path_retry_floor_is_local_idempotent_and_monotonic(self) -> None:
        env=FakePathEnvironment()
        move_to=env.instance.namespaces[0].move_to
        tool=move_to.__wrapped__
        original=tool.get_path

        applied=enforce_pathfinding_retry_floor(
            env,
            minimum_attempts=40,
        )
        self.assertEqual(applied,40)

        tool.get_path(7,max_attempts=5)
        tool.get_path(8,max_attempts=60)
        self.assertEqual(original.attempts,[40,60])

        applied_again=enforce_pathfinding_retry_floor(
            env,
            minimum_attempts=20,
        )
        self.assertEqual(applied_again,40)
        tool.get_path(9)
        self.assertEqual(original.attempts[-1],40)

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


    def test_rejected_fle_checkpoint_repairs_exact_missing_entity(self) -> None:
        rows=[
            {
                "name": '"stone-furnace"',
                "position":{"x":"20","y":"69"},
                "direction":0,
            },
            {
                "name": '"stone-furnace"',
                "position":{"x":"-63","y":"69"},
                "direction":0,
            },
        ]
        checkpoint=FakeCheckpoint(rows)
        env=FakeRollbackEnvironment(checkpoint,lose_name="never")
        env.namespace.rows=[dict(rows[0])]
        original_reset=env.reset
        def reset_with_one_missing(*,options=None,seed=None):
            result=original_reset(options=options,seed=seed)
            if options is not None and options.get("game_state") is checkpoint:
                env.namespace.rows=[dict(rows[0])]
            return result
        env.reset=reset_with_one_missing
        executor=TransactionalFLEExecutor(env,action_factory=fake_action_factory)
        executor.game_state=checkpoint

        rejected=executor.execute("bad",accept=lambda _:False)

        self.assertFalse(rejected.accepted)
        integrity=executor.rollback_integrity_snapshot()
        self.assertIsNotNone(integrity)
        assert integrity is not None
        self.assertEqual(integrity["status"],"repaired")
        self.assertEqual(integrity["replayed_entities"],1)
        identities={
            (
                str(row["name"]).replace('"',""),
                float(row["position"]["x"]),
                float(row["position"]["y"]),
            )
            for row in env.namespace.rows
        }
        self.assertIn(("stone-furnace",20.0,69.0),identities)
        self.assertIn(("stone-furnace",-63.0,69.0),identities)


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
