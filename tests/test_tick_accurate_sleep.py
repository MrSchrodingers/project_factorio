import unittest
from unittest.mock import patch

from factorio_ai_lab.integrations.fle import bind_tick_accurate_sleep_tool


class FakeNamespace:
    pass


class FakeRcon:
    def __init__(self) -> None:
        self.ticks=[100,140,190,240,280]
        self.commands=[]

    def send_command(self,command: str) -> str:
        self.commands.append(command)
        if command=="/sc rcon.print(game.tick)":
            return str(self.ticks.pop(0))
        if command.startswith("/sc storage.elapsed_ticks"):
            return ""
        raise AssertionError(command)


class FakeInstance:
    def __init__(self) -> None:
        self.rcon_client=FakeRcon()
        self.namespaces=[FakeNamespace()]


class FakeEnvironment:
    def __init__(self) -> None:
        self.instance=FakeInstance()
        self.unwrapped=self


class TickAccurateSleepTests(unittest.TestCase):
    def test_waits_until_real_factorio_ticks_reach_target(self) -> None:
        env=FakeEnvironment()
        with patch("factorio_ai_lab.integrations.fle.time.sleep") as sleeper:
            name=bind_tick_accurate_sleep_tool(
                env,
                poll_interval_seconds=0.01,
            )
            result=env.instance.namespaces[0].sleep(3)

        self.assertEqual(name,"sleep")
        self.assertTrue(result)
        self.assertEqual(sleeper.call_count,4)
        self.assertEqual(
            env.instance.rcon_client.commands[-1],
            "/sc storage.elapsed_ticks = (storage.elapsed_ticks or 0) + 180",
        )

    def test_binding_does_not_advance_ticks(self) -> None:
        env=FakeEnvironment()
        bind_tick_accurate_sleep_tool(env)
        self.assertEqual(env.instance.rcon_client.commands,[])


if __name__=="__main__":
    unittest.main()
