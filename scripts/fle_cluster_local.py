#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import yaml
from fle.cluster.run_envs import ComposeGenerator

# Measured on this host (docker logs of fle-local-factorio_0-1, two boots):
# the default_lab_scenario ships its map inside blueprint.zip as a 3652547
# byte level.dat, and every boot loads that same file at tick 360199. Map
# generation never runs for it, so --map-gen-seed and the seed field of
# map-gen-settings.json change nothing. Only a scenario that generates its
# map honours a seed.
GENERATING_SCENARIOS = frozenset({"open_world"})
CANNED_MAP_SCENARIOS = frozenset({"default_lab_scenario"})

DOCKER_LOG_MAX_SIZE = "50m"
DOCKER_LOG_MAX_FILE = "3"
AUTOSAVE_INTERVAL_MINUTES = 5
AUTOSAVE_SLOTS = 3
PERSISTENT_VOLUME_PREFIX = "factorio-ai-lab-fle"

_MAP_GEN_SEED_FLAG = re.compile(r"--map-gen-seed\s+\d+")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def compose_path(root: Path) -> Path:
    return root / ".fle-local" / "docker-compose.yml"


def command_with_map_gen_seed(command: str, seed: int) -> str:
    """Return the service command carrying exactly one --map-gen-seed."""
    replacement = f"--map-gen-seed {int(seed)}"
    if _MAP_GEN_SEED_FLAG.search(command):
        return _MAP_GEN_SEED_FLAG.sub(replacement, command, count=1)
    anchor = " --mod-directory"
    if anchor not in command:
        raise ValueError("service command has no anchor to insert --map-gen-seed")
    return command.replace(anchor, f" {replacement}{anchor}", 1)


def command_with_persistent_world(command: str, scenario: str) -> str:
    """Load the newest persisted save, falling back to the scenario once."""

    startup=f"--start-server-load-scenario {scenario}"
    if command.count(startup)!=1:
        raise ValueError(
            f"service command must contain exactly one {startup!r}"
        )
    exec_anchor="exec /opt/factorio/bin/x64/factorio "
    if exec_anchor not in command:
        raise ValueError("service command has no Factorio exec anchor")
    bootstrap=(
        'if find /factorio/saves -maxdepth 1 -type f -name "*.zip" '
        '-print -quit 2>/dev/null | grep -q .; '
        'then CORTEX_START_MODE="--start-server-load-latest"; '
        f'else CORTEX_START_MODE="--start-server-load-scenario {scenario}"; fi; '
    )
    command=command.replace(startup,"$$CORTEX_START_MODE",1)
    return command.replace(exec_anchor,bootstrap+exec_anchor,1)


def _persistent_config_dir(root: Path) -> Path:
    return root/".fle-local"/"config"


def _prepare_persistent_config(root: Path, package_config_dir: Path) -> Path:
    target=_persistent_config_dir(root)
    target.mkdir(parents=True,exist_ok=True)
    shutil.copytree(package_config_dir,target,dirs_exist_ok=True)
    settings_path=target/"server-settings.json"
    settings=json.loads(settings_path.read_text(encoding="utf-8"))
    settings["autosave_interval"]=AUTOSAVE_INTERVAL_MINUTES
    settings["autosave_slots"]=AUTOSAVE_SLOTS
    settings["autosave_only_on_server"]=True
    settings["non_blocking_saving"]=False
    settings_path.write_text(
        json.dumps(settings,indent=2,sort_keys=False)+"\n",
        encoding="utf-8",
    )
    return target


def _persistent_volume_name(service_name: str) -> str:
    return f"{PERSISTENT_VOLUME_PREFIX}-{service_name}-data"


def apply_map_gen_seeds(
    data: dict,
    seeds: list[int],
    *,
    scenario: str,
) -> dict:
    """Give each instance its own map generator seed.

    Refuses the canned-map scenario: there the flag is accepted by Factorio
    and ignored by the world, which would produce N identical maps wearing N
    seed labels - the exact failure this option exists to end.
    """
    if scenario in CANNED_MAP_SCENARIOS:
        raise ValueError(
            f"scenario {scenario!r} ships a pre-generated map; a map-gen seed "
            "would be ignored and every instance would share one terrain"
        )
    if scenario not in GENERATING_SCENARIOS:
        raise ValueError(f"scenario {scenario!r} is not known to generate its map")
    services = data["services"]
    names = sorted(services)
    if len(seeds) != len(names):
        raise ValueError(
            f"got {len(seeds)} seeds for {len(names)} instances; "
            "one distinct seed per instance is required"
        )
    if len(set(seeds)) != len(seeds):
        raise ValueError(f"seeds must be distinct, got {seeds}")
    for name, seed in zip(names, seeds, strict=True):
        services[name]["command"] = command_with_map_gen_seed(
            services[name]["command"],
            seed,
        )
    return data


def generate_compose(
    root: Path,
    instances: int,
    scenario: str,
    seeds: list[int] | None = None,
) -> Path:
    if not 1 <= instances <= 8:
        raise ValueError("instances must be in [1, 8] for the local research profile")

    state_dir = root / ".fle-local"
    state_dir.mkdir(parents=True, exist_ok=True)

    package_root = resources.files("fle.cluster")
    generator = ComposeGenerator(
        scenario=scenario,
        state_dir=state_dir,
        work_dir=root,
        pkg_scenarios_dir=Path(package_root / "scenarios"),
        pkg_config_dir=Path(package_root / "config"),
    )
    data = generator.compose_dict(instances)
    persistent_config=_prepare_persistent_config(
        root,
        Path(package_root/"config"),
    )
    declared_volumes=data.setdefault("volumes",{})

    for service_name,service in data["services"].items():
        local_ports: list[str] = []
        for mapping in service["ports"]:
            host, container_proto = mapping.split(":", 1)
            local_ports.append(f"127.0.0.1:{host}:{container_proto}")
        service["ports"] = local_ports
        service["restart"] = "unless-stopped"
        service["logging"] = {
            "driver": "json-file",
            "options": {
                "max-size": DOCKER_LOG_MAX_SIZE,
                "max-file": DOCKER_LOG_MAX_FILE,
            },
        }
        service["command"]=command_with_persistent_world(
            service["command"],
            scenario,
        )
        volume_key=f"{service_name}_data"
        declared_volumes[volume_key]={
            "name":_persistent_volume_name(service_name),
        }
        mounts=service.setdefault("volumes",[])
        mounts=[
            mount
            for mount in mounts
            if not (
                isinstance(mount,dict)
                and mount.get("target")=="/factorio"
            )
        ]
        for mount in mounts:
            if (
                isinstance(mount,dict)
                and mount.get("target")=="/opt/factorio/config"
            ):
                mount["source"]=str(persistent_config)
        mounts.append({
            "type":"volume",
            "source":volume_key,
            "target":"/factorio",
        })
        service["volumes"]=mounts

    if seeds:
        apply_map_gen_seeds(data, seeds, scenario=scenario)

    path = compose_path(root)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def compose(root: Path, *args: str) -> None:
    path = compose_path(root)
    subprocess.run(
        ["docker", "compose", "-f", str(path), *args],
        cwd=root,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the FLE Factorio cluster bound to localhost only."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start")
    start.add_argument("-n", "--instances", type=int, default=1)
    start.add_argument(
        "-s",
        "--scenario",
        choices=("default_lab_scenario", "open_world"),
        default="default_lab_scenario",
    )
    start.add_argument(
        "--map-gen-seeds",
        type=int,
        nargs="+",
        default=None,
        help=(
            "one distinct map generator seed per instance; only valid for a "
            "scenario that generates its map"
        ),
    )

    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("config")

    args = parser.parse_args()
    root = project_root()

    if args.command == "start":
        path = generate_compose(
            root,
            args.instances,
            args.scenario,
            seeds=args.map_gen_seeds,
        )
        print(f"compose={path}")
        compose(root, "up", "-d")
        compose(root, "ps")
        return 0

    if args.command == "config":
        path = generate_compose(root, 1, "default_lab_scenario")
        print(path.read_text())
        return 0

    if args.command == "stop":
        if compose_path(root).exists():
            compose(root, "down")
        return 0

    if args.command == "status":
        if compose_path(root).exists():
            compose(root, "ps")
        else:
            print("cluster_not_configured")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
