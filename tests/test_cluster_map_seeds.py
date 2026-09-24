"""Per-instance map generator seeds in the local cluster compose.

The FLE generator hard-codes one seed (44340) for every instance and only
emits the flag for the open_world scenario. Distinct worlds need one distinct
seed per service, and the canned lab scenario has to be refused outright:
there Factorio accepts the flag and loads its shipped map anyway.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

import pytest
import yaml

pytest.importorskip("yaml")
pytest.importorskip("fle.cluster.run_envs")

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fle_cluster_local.py"


def _module() -> Any:
    spec = importlib.util.spec_from_file_location("fle_cluster_local", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLUSTER = _module()

_BASE_COMMAND = (
    "/bin/sh -c 'rm -rf /opt/factorio/data/space-age && exec "
    "/opt/factorio/bin/x64/factorio --start-server-load-scenario open_world "
    "--port 34197 --rcon-port 27015 --use-server-whitelist "
    "--mod-directory /opt/factorio/mods'"
)


def _compose(instances: int = 2) -> dict[str, Any]:
    return {
        "services": {
            f"factorio_{index}": {"command": _BASE_COMMAND}
            for index in range(instances)
        }
    }


def test_seed_is_inserted_when_absent() -> None:
    command = CLUSTER.command_with_map_gen_seed(_BASE_COMMAND, 20260931)
    assert "--map-gen-seed 20260931 --mod-directory" in command
    assert command.count("--map-gen-seed") == 1


def test_seed_replaces_the_hardcoded_value() -> None:
    seeded = _BASE_COMMAND.replace(
        " --mod-directory", " --map-gen-seed 44340 --mod-directory"
    )
    command = CLUSTER.command_with_map_gen_seed(seeded, 7)
    assert "--map-gen-seed 7 " in command
    assert "44340" not in command
    assert command.count("--map-gen-seed") == 1


def test_each_instance_gets_its_own_seed() -> None:
    data = CLUSTER.apply_map_gen_seeds(
        _compose(3),
        [11, 12, 13],
        scenario="open_world",
    )
    seeds = [
        service["command"].split("--map-gen-seed ")[1].split(" ")[0]
        for _, service in sorted(data["services"].items())
    ]
    assert seeds == ["11", "12", "13"]


def test_canned_scenario_is_refused() -> None:
    with pytest.raises(ValueError, match="pre-generated map"):
        CLUSTER.apply_map_gen_seeds(
            _compose(2),
            [11, 12],
            scenario="default_lab_scenario",
        )


def test_repeated_seeds_are_refused() -> None:
    with pytest.raises(ValueError, match="must be distinct"):
        CLUSTER.apply_map_gen_seeds(_compose(2), [11, 11], scenario="open_world")


def test_seed_count_must_match_instance_count() -> None:
    with pytest.raises(ValueError, match="one distinct seed per instance"):
        CLUSTER.apply_map_gen_seeds(_compose(3), [11, 12], scenario="open_world")

def test_generated_factorio_services_restart_unless_stopped(tmp_path) -> None:
    path = CLUSTER.generate_compose(
        tmp_path,
        instances=1,
        scenario="default_lab_scenario",
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert data["services"]
    assert all(
        service["restart"] == "unless-stopped"
        for service in data["services"].values()
    )
    assert all(
        service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "50m", "max-file": "3"},
        }
        for service in data["services"].values()
    )
