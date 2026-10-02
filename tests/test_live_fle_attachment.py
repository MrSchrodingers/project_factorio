from __future__ import annotations

from factorio_ai_lab.integrations.fle import (
    _LIVE_ATTACH_INIT_SCRIPTS,
    _bootstrap_live_fle_runtime,
)


class FakeRcon:
    def __init__(self) -> None:
        self.commands: list[str]=[]

    def send_command(self,command: str) -> None:
        self.commands.append(command)


class FakeNamespace:
    def __init__(self) -> None:
        self.agent_counts: list[int]=[]

    def _create_agent_characters(self,count: int) -> None:
        self.agent_counts.append(count)


class FakeLuaScripts:
    def __init__(self) -> None:
        self.loaded: list[str]=[]

    def load_init_into_game(self,name: str) -> None:
        self.loaded.append(name)


class FakeInstance:
    def __init__(self) -> None:
        self.rcon_client=FakeRcon()
        self.first_namespace=FakeNamespace()
        self.lua_script_manager=FakeLuaScripts()
        self.num_agents=1


def test_live_attach_bootstrap_only_reloads_runtime_functions() -> None:
    instance=FakeInstance()

    _bootstrap_live_fle_runtime(instance,fast=True)

    assert instance.rcon_client.commands==["/sc storage.fast = true"]
    assert instance.first_namespace.agent_counts==[1]
    assert tuple(instance.lua_script_manager.loaded)==_LIVE_ATTACH_INIT_SCRIPTS


def test_live_attach_source_never_calls_world_reset() -> None:
    import inspect

    from factorio_ai_lab.integrations.fle import attach_live_factorio_environment

    source=inspect.getsource(attach_live_factorio_environment)
    assert "LiveAttachFactorioInstance" in source
    assert "_bootstrap_live_fle_runtime(self,fast=fast)" in source
    assert "self.first_namespace._reset(" not in source
    assert "self._generate_chunks(" not in source
    assert "self.first_namespace._clear_collision_boxes(" not in source
