import unittest

from factorio_ai_lab.integrations.fle import fast_reposition


class Position:
    def __init__(self, *, x: float, y: float) -> None:
        self.x = x
        self.y = y


class FakeRcon:
    def __init__(self) -> None:
        self.command = ""

    def send_command(self, command: str) -> str:
        self.command = command
        return "12.5,34.5"


class FakeNamespace:
    def __init__(self) -> None:
        self.player_location = Position(x=0.0, y=0.0)


class FakeInstance:
    def __init__(self) -> None:
        self.rcon_client = FakeRcon()
        self.namespaces = [FakeNamespace()]


class FakeEnvironment:
    def __init__(self) -> None:
        self.instance = FakeInstance()
        self.unwrapped = self


class FastRepositionTests(unittest.TestCase):
    def test_repositions_synthetic_agent_character(self) -> None:
        env = FakeEnvironment()
        result = fast_reposition(env, x=12.5, y=34.5)
        self.assertEqual((result.x, result.y), (12.5, 34.5))
        self.assertIn("storage.agent_characters[1]", env.instance.rcon_client.command)
        position = env.instance.namespaces[0].player_location
        self.assertEqual((position.x, position.y), (12.5, 34.5))

    def test_rejects_non_finite_coordinate(self) -> None:
        with self.assertRaises(ValueError):
            fast_reposition(FakeEnvironment(), x=float("nan"), y=1.0)


if __name__ == "__main__":
    unittest.main()
