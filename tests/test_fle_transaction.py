import base64
import json
import unittest
import zlib
from dataclasses import dataclass

from factorio_ai_lab.integrations.fle import (
    TransactionalFLEExecutor,
    bind_safe_score_tool,
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
    def __init__(self, rows, *, force_empty_save: bool=False) -> None:
        self.rows=[dict(row) for row in rows]
        self.loaded_batches=[]
        self.force_empty_save=force_empty_save

    def _save_entity_state(self, compress=True, encode=True):
        assert compress is True
        assert encode is True
        return _encode_entities([] if self.force_empty_save else self.rows)

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


class FakeRconClient:
    def __init__(self, namespace, *, production=None) -> None:
        self.namespace=namespace
        self.production=production or {
            "items":{"produced":{},"consumed":{}},
            "fluids":{"produced":{},"consumed":{}},
        }

    def send_command(self, command):
        production_target="CORTEX_PRODUCTION_TARGET|"
        if production_target in command:
            encoded=command.split(production_target,1)[1].strip()
            self.production=json.loads(
                base64.b64decode(encoded).decode("utf-8")
            )
            return "CORTEX_PRODUCTION_RESTORE_OK"
        if "CORTEX_PRODUCTION_CAPTURE|" in command:
            return (
                "CORTEX_PRODUCTION_CAPTURE|"
                +json.dumps(self.production,sort_keys=True)
            )
        marker="CORTEX_REMOVE_TARGET|"
        if marker in command:
            raw=command.split(marker,1)[1].strip()
            name,sx,sy,sd=raw.split("|",3)
            target=(name,float(sx),float(sy),int(sd))
            kept=[]
            removed=0
            for row in self.namespace.rows:
                identity=(
                    str(row["name"]).replace('"',""),
                    float(row["position"]["x"]),
                    float(row["position"]["y"]),
                    int(row.get("direction") or 0),
                )
                if removed==0 and identity==target:
                    removed=1
                    continue
                kept.append(row)
            self.namespace.rows=kept
            return f"CORTEX_REMOVE|{removed}"
        assert "CORTEX_CAPTURE_BEGIN" in command
        lines=["CORTEX_CAPTURE_BEGIN"]
        for row in self.namespace.rows:
            name=str(row["name"]).replace('"',"")
            if name=="character":
                continue
            position=row["position"]
            direction=int(row.get("direction") or 0)
            lines.append(
                f"CORTEX_ENTITY|{name}|{position['x']}|{position['y']}|{direction}"
            )
        lines.append("CORTEX_CAPTURE_END")
        return "\n".join(lines)


class FakeSnapshotInstance:
    def __init__(self, namespace, *, rcon_client=None) -> None:
        self.first_namespace=namespace
        self.rcon_client=rcon_client


class FakeRollbackEnvironment(FakeEnvironment):
    def __init__(
        self,
        checkpoint,
        *,
        lose_name: str,
        force_empty_save: bool=False,
    ) -> None:
        super().__init__()
        self.checkpoint=checkpoint
        self.lose_name=lose_name
        self.namespace=FakeSnapshotNamespace(
            [
                row for row in checkpoint.rows
                if str(row["name"]).replace('"',"") != lose_name
            ],
            force_empty_save=force_empty_save,
        )
        rcon=FakeRconClient(self.namespace) if force_empty_save else None
        self.instance=FakeSnapshotInstance(self.namespace,rcon_client=rcon)
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


class FakeScoreTool:
    def __init__(self,response) -> None:
        self.response=response

    def execute(self,*args,**kwargs):
        return self.response,0.0


class FakeScoreNamespace:
    def __init__(self,response) -> None:
        tool=FakeScoreTool(response)

        def score_wrapper(*args,**kwargs):
            response,_elapsed=tool.execute(*args,**kwargs)
            return response

        score_wrapper.__wrapped__=tool
        self.score=score_wrapper


class FakeScoreInstance:
    def __init__(self,response,initial_score: float) -> None:
        self.initial_score=initial_score
        self.namespaces=[FakeScoreNamespace(response)]


class FakeScoreEnvironment:
    def __init__(self,response,initial_score: float=0.0) -> None:
        self.instance=FakeScoreInstance(response,initial_score)
        self.unwrapped=self


class RaisingEnvironment(FakeEnvironment):
    def step(self,action):
        assert isinstance(action,FakeAction)
        self.last_action=action
        self.state=f"{self.state}|{action.code}"
        raise KeyError("player")


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

    def test_safe_score_binding_handles_missing_player(self) -> None:
        env=FakeScoreEnvironment({"automated":7},initial_score=12)
        name=bind_safe_score_tool(env)

        player,automated=env.instance.namespaces[0].score()

        self.assertEqual(name,"score")
        self.assertEqual(player,0.0)
        self.assertEqual(automated,7.0)

    def test_safe_score_binding_preserves_present_player_semantics(self) -> None:
        env=FakeScoreEnvironment({"player":20,"automated":7},initial_score=12)
        bind_safe_score_tool(env)

        player,automated=env.instance.namespaces[0].score()

        self.assertEqual(player,8.0)
        self.assertEqual(automated,7.0)

    def test_step_exception_restores_checkpoint_before_reraising(self) -> None:
        env=RaisingEnvironment()
        executor=TransactionalFLEExecutor(env,action_factory=fake_action_factory)
        executor.game_state="checkpoint"
        env.state="checkpoint"

        with self.assertRaisesRegex(KeyError,"player"):
            executor.execute(
                "mutate()",
                accept=lambda _:True,
                use_checkpoint_for_action=False,
            )

        self.assertEqual(env.state,"checkpoint")
        self.assertEqual(executor.game_state,"checkpoint")
        integrity=executor.rollback_integrity_snapshot()
        self.assertIsNotNone(integrity)
        assert integrity is not None
        self.assertEqual(integrity["status"],"not_applicable")

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


    def test_rejected_checkpoint_uses_rcon_when_fle_saver_is_empty(self) -> None:
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
        env=FakeRollbackEnvironment(
            checkpoint,
            lose_name="never",
            force_empty_save=True,
        )
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
        self.assertEqual(
            identities,
            {
                ("stone-furnace",20.0,69.0),
                ("stone-furnace",-63.0,69.0),
            },
        )


    def test_rejected_checkpoint_removes_exact_unexpected_entity(self) -> None:
        rows=[
            {
                "name": '"stone-furnace"',
                "position":{"x":"20","y":"69"},
                "direction":0,
            },
        ]
        extra={
            "name": '"stone-furnace"',
            "position":{"x":"-63","y":"69"},
            "direction":0,
        }
        checkpoint=FakeCheckpoint(rows)
        env=FakeRollbackEnvironment(checkpoint,lose_name="never")
        env.instance.rcon_client=FakeRconClient(env.namespace)
        original_reset=env.reset

        def reset_with_extra(*,options=None,seed=None):
            result=original_reset(options=options,seed=seed)
            if options is not None and options.get("game_state") is checkpoint:
                env.namespace.rows=[dict(rows[0]),dict(extra)]
            return result

        env.reset=reset_with_extra
        executor=TransactionalFLEExecutor(env,action_factory=fake_action_factory)
        executor.game_state=checkpoint

        rejected=executor.execute("bad",accept=lambda _:False)

        self.assertFalse(rejected.accepted)
        integrity=executor.rollback_integrity_snapshot()
        self.assertIsNotNone(integrity)
        assert integrity is not None
        self.assertEqual(integrity["status"],"repaired")
        self.assertEqual(integrity["removed_entities"],1)
        self.assertEqual(
            integrity["unexpected_before_repair"],
            [["stone-furnace",-63.0,69.0,0]],
        )
        self.assertEqual(integrity["unexpected_after_repair"],[])
        identities={
            (
                str(row["name"]).replace('"',""),
                float(row["position"]["x"]),
                float(row["position"]["y"]),
                int(row.get("direction") or 0),
            )
            for row in env.namespace.rows
        }
        self.assertEqual(identities,{("stone-furnace",20.0,69.0,0)})



    def test_rejected_checkpoint_restores_cumulative_production_totals(self) -> None:
        rows=[
            {
                "name": '"stone-furnace"',
                "position":{"x":"20","y":"69"},
                "direction":0,
            },
        ]
        checkpoint=FakeCheckpoint(rows)
        env=FakeRollbackEnvironment(checkpoint,lose_name="never")
        baseline={
            "items":{
                "produced":{"iron-plate":192.0,"coal":954.0},
                "consumed":{"coal":137.0},
            },
            "fluids":{
                "produced":{"steam":3190.0},
                "consumed":{"steam":1490.0},
            },
        }
        rcon=FakeRconClient(env.namespace,production=baseline)
        env.instance.rcon_client=rcon
        original_reset=env.reset

        def reset_and_clear_stats(*,options=None,seed=None):
            result=original_reset(options=options,seed=seed)
            if options is not None and options.get("game_state") is checkpoint:
                rcon.production={
                    "items":{
                        "produced":{"coal":1.0},
                        "consumed":{"coal":5.0},
                    },
                    "fluids":{
                        "produced":{"steam":195.0},
                        "consumed":{"steam":10.0},
                    },
                }
            return result

        env.reset=reset_and_clear_stats
        executor=TransactionalFLEExecutor(
            env,
            action_factory=fake_action_factory,
        )
        executor.game_state=checkpoint

        rejected=executor.execute("bad",accept=lambda _:False)

        self.assertFalse(rejected.accepted)
        integrity=executor.rollback_integrity_snapshot()
        self.assertIsNotNone(integrity)
        assert integrity is not None
        telemetry=integrity["production_statistics"]
        self.assertEqual(telemetry["status"],"restored")
        self.assertTrue(telemetry["cumulative_totals_restored"])
        self.assertFalse(telemetry["history_windows_restored"])
        self.assertEqual(rcon.production,baseline)

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
