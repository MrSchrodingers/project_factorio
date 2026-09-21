#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.resources as resources
import subprocess
from pathlib import Path

import yaml

from fle.cluster.run_envs import ComposeGenerator


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def compose_path(root: Path) -> Path:
    return root / ".fle-local" / "docker-compose.yml"


def generate_compose(root: Path, instances: int, scenario: str) -> Path:
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
        service["restart"] = "no"

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

    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("config")

    args = parser.parse_args()
    root = project_root()

    if args.command == "start":
        path = generate_compose(root, args.instances, args.scenario)
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
