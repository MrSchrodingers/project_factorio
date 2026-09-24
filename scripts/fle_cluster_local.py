#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
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

    for service in data["services"].values():
        local_ports: list[str] = []
        for mapping in service["ports"]:
            host, container_proto = mapping.split(":", 1)
            local_ports.append(f"127.0.0.1:{host}:{container_proto}")
        service["ports"] = local_ports
        service["restart"] = "unless-stopped"

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
