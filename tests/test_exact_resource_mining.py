from __future__ import annotations

import pytest

from factorio_ai_lab.integrations.fle import (
    bind_exact_resource_mining_tool,
    mine_exact_resource,
)


class Position:
    def __init__(self, *, x: float, y: float) -> None:
        self.x=x
        self.y=y


class FakeRcon:
    def __init__(self, response: str="0,5,5,5") -> None:
        self.response=response
        self.command=""

    def send_command(self, command: str) -> str:
        self.command=command
        return self.response


class FakeNamespace:
    pass


class FakeInstance:
    def __init__(self, response: str="0,5,5,5") -> None:
        self.rcon_client=FakeRcon(response)
        self.namespaces=[FakeNamespace()]


class FakeEnvironment:
    def __init__(self, response: str="0,5,5,5") -> None:
        self.instance=FakeInstance(response)
        self.unwrapped=self


def test_exact_resource_mining_uses_native_character_mining_and_inventory_delta() -> None:
    env=FakeEnvironment()
    result=mine_exact_resource(
        env,x=-46.5,y=-0.5,resource_name="stone",quantity=5,
    )
    assert result.inventory_growth==5
    assert result.inventory_before==0
    assert result.inventory_after==5
    command=env.instance.rcon_client.command
    assert "storage.agent_characters[1]" in command
    assert "p.mine_entity(best)" in command
    assert "after_attempt<=before_attempt" in command
    assert "p.get_item_count(name)" in command
    assert 'local name="stone"' in command
    assert "player.insert" not in command
    assert "p.insert" not in command


def test_exact_resource_mining_rejects_missing_physical_inventory_growth() -> None:
    env=FakeEnvironment("0,0,0,5")
    with pytest.raises(RuntimeError,match="native mining produced 0/5 stone"):
        mine_exact_resource(
            env,x=-46.5,y=-0.5,resource_name="stone",quantity=5,
        )


def test_exact_resource_mining_restricts_resources_and_bounds() -> None:
    with pytest.raises(ValueError,match="unsupported exact resource"):
        mine_exact_resource(
            FakeEnvironment(),
            x=0,y=0,resource_name="wood",quantity=1,
        )
    with pytest.raises(ValueError,match="quantity must be within"):
        mine_exact_resource(
            FakeEnvironment(),
            x=0,y=0,resource_name="stone",quantity=0,
        )


def test_bind_exact_resource_mining_is_inert_until_called() -> None:
    env=FakeEnvironment()
    name=bind_exact_resource_mining_tool(env)
    assert name=="cortex_mine_exact_resource"
    assert env.instance.rcon_client.command==""
    growth=env.instance.namespaces[0].cortex_mine_exact_resource(
        Position(x=-46.5,y=-0.5),"stone",quantity=5,radius=1.5,
    )
    assert growth==5
