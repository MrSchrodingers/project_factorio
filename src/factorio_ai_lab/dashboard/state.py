from __future__ import annotations

import csv
import json
import socket
import subprocess
import threading
import time
import urllib.request
from collections import defaultdict, deque
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
RUNTIME_CONFIG = RUNS_DIR / "runtime_config.json"


DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "poll_interval_s": 1.5,
    "astar_turn_penalty": 0.10,
    "llm_provider": "local-qwen",
    "llm_temperature": 0.20,
    "llm_max_tokens": 768,
    "ucb_exploration": 2.0,
    "learning_algorithm": "ucb1",
    "neural_policy": "cnn_attention",
    "world_model": "explicit_residual",
}


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value), depth + 1)
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth + 1) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(), depth + 1)
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value), depth + 1)
    return str(value)


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), *args],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _mean_numeric(rows: list[dict[str, str]], key: str) -> float | None:
    if not rows:
        return None
    return sum(float(row[key]) for row in rows) / len(rows)


def _memory_status() -> dict[str, float]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0])
    except (OSError, ValueError):
        return {}
    total = values.get("MemTotal", 0) / 1024
    available = values.get("MemAvailable", 0) / 1024
    return {
        "total_mib": round(total, 1),
        "available_mib": round(available, 1),
        "used_mib": round(max(total - available, 0.0), 1),
    }


class RuntimeConfigStore:
    def __init__(self, path: Path = RUNTIME_CONFIG) -> None:
        self.path = path
        self.lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        with self.lock:
            data = dict(DEFAULT_RUNTIME_CONFIG)
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text())
                    if isinstance(loaded, dict):
                        data.update(loaded)
                except (OSError, json.JSONDecodeError):
                    pass
            return data

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_RUNTIME_CONFIG)
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unknown runtime parameters: {sorted(unknown)}")
        with self.lock:
            current = dict(DEFAULT_RUNTIME_CONFIG)
            if self.path.exists():
                try:
                    loaded = json.loads(self.path.read_text())
                    if isinstance(loaded, dict):
                        current.update(loaded)
                except (OSError, json.JSONDecodeError):
                    pass
            current.update(changes)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(current, indent=2, sort_keys=True) + "\n"
            )
            return current


class FactorioObserver:
    """Read-only FLE observer. It never resets the world or submits player actions."""

    def __init__(self, host: str = "127.0.0.1", port: int = 27000) -> None:
        self.host = host
        self.port = port
        self._instance: Any | None = None
        self._lock = threading.Lock()
        self._last_error: str | None = None

    def connected(self) -> bool:
        return _port_open(self.host, self.port)

    def _discard_instance_unlocked(self) -> None:
        if self._instance is None:
            return
        try:
            self._instance.cleanup()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"cleanup {type(exc).__name__}: {exc}"
        finally:
            self._instance = None

    def close(self) -> None:
        with self._lock:
            self._discard_instance_unlocked()

    def _ensure_instance(self) -> Any:
        if self._instance is not None:
            return self._instance
        from fle.env import FactorioInstance

        self._instance = FactorioInstance(
            address=self.host,
            tcp_port=self.port,
            num_agents=1,
            fast=True,
            cache_scripts=True,
            inventory={},
            all_technologies_researched=False,
        )
        return self._instance

    def snapshot(self) -> dict[str, Any]:
        started = time.perf_counter()
        if not self.connected():
            self.close()
            return {
                "connected": False,
                "entities": [],
                "entity_count": 0,
                "error": "RCON unavailable",
                "latency_ms": 0.0,
            }

        with self._lock:
            try:
                instance = self._ensure_instance()
                namespace = instance.namespace
                entities = namespace._save_entity_state(
                    distance=500,
                    player_entities=True,
                    resource_entities=False,
                    items_on_ground=False,
                    encode=False,
                    compress=False,
                )
                production = namespace._get_production_stats()
                ticks = instance.get_elapsed_ticks()
                safe_entities = _json_safe(entities)
                if not isinstance(safe_entities, list):
                    safe_entities = []
                self._last_error = None
                return {
                    "connected": True,
                    "tick": _json_safe(ticks),
                    "entities": safe_entities,
                    "entity_count": len(safe_entities),
                    "production": _json_safe(production),
                    "latency_ms": round(
                        (time.perf_counter() - started) * 1000.0, 2
                    ),
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._discard_instance_unlocked()
                return {
                    "connected": False,
                    "entities": [],
                    "entity_count": 0,
                    "latency_ms": round(
                        (time.perf_counter() - started) * 1000.0, 2
                    ),
                    "error": self._last_error,
                }


class DashboardState:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.config = RuntimeConfigStore()
        self.factorio = FactorioObserver()
        self.history: deque[dict[str, Any]] = deque(maxlen=600)
        self.history_lock = threading.Lock()

    def close(self) -> None:
        self.factorio.close()

    def llm_status(self) -> dict[str, Any]:
        url = "http://127.0.0.1:18081/v1/models"
        try:
            with urllib.request.urlopen(url, timeout=0.35) as response:
                payload = json.loads(response.read().decode())
            models = [
                item.get("id", "unknown")
                for item in payload.get("data", [])
                if isinstance(item, dict)
            ]
            return {
                "connected": True,
                "base_url": "http://127.0.0.1:18081/v1",
                "models": models,
                "provider": "llama.cpp",
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return {
                "connected": False,
                "base_url": "http://127.0.0.1:18081/v1",
                "models": [],
                "provider": "llama.cpp",
                "error": f"{type(exc).__name__}: {exc}",
            }

    def status(self) -> dict[str, Any]:
        return {
            "project": "Factorio AI Lab",
            "git_sha": _git_value("rev-parse", "--short", "HEAD"),
            "branch": _git_value("branch", "--show-current"),
            "uptime_s": round(time.time() - self.started_at, 1),
            "factorio": {
                "connected": self.factorio.connected(),
                "host": "127.0.0.1",
                "rcon_port": 27000,
            },
            "llm": self.llm_status(),
            "memory": _memory_status(),
            "runtime": self.config.read(),
        }

    def sample(self) -> dict[str, Any]:
        world = self.factorio.snapshot()
        point = {
            "timestamp": time.time(),
            "tick": world.get("tick"),
            "entity_count": world.get("entity_count", 0),
            "probe_latency_ms": world.get("latency_ms", 0.0),
            "factorio_connected": world.get("connected", False),
        }
        with self.history_lock:
            self.history.append(point)
        return {
            "timestamp": point["timestamp"],
            "status": self.status(),
            "world": world,
            "history": self.history_data(),
            "learning": self.learning_data(),
        }

    def history_data(self) -> list[dict[str, Any]]:
        with self.history_lock:
            return list(self.history)

    def learning_data(self) -> dict[str, Any]:
        history_path = RUNS_DIR / "turn_penalty_learning.jsonl"
        summary_path = RUNS_DIR / "turn_penalty_learning_summary.json"
        rows: list[dict[str, Any]] = []
        if history_path.exists():
            try:
                lines = history_path.read_text().splitlines()[-500:]
                rows = [json.loads(line) for line in lines if line.strip()]
            except (OSError, json.JSONDecodeError):
                rows = []
        summary: dict[str, Any] = {}
        if summary_path.exists():
            try:
                loaded = json.loads(summary_path.read_text())
                if isinstance(loaded, dict):
                    summary = loaded
            except (OSError, json.JSONDecodeError):
                pass
        return {"history": rows, "summary": summary}

    def routing_sweep(self) -> dict[str, Any]:
        candidates = sorted(
            RUNS_DIR.glob("routing_sweep*.csv"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return {"source": None, "rows": []}

        source = candidates[0]
        grouped: dict[float, list[dict[str, str]]] = defaultdict(list)
        with source.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                grouped[float(row["turn_penalty"])].append(row)

        output: list[dict[str, Any]] = []
        for penalty, rows in sorted(grouped.items()):
            solved = [row for row in rows if row["success"].lower() == "true"]
            count = len(rows)
            solved_count = len(solved)

            output.append(
                {
                    "turn_penalty": penalty,
                    "episodes": count,
                    "success_rate": solved_count / count if count else 0.0,
                    "mean_route_length": _mean_numeric(solved, "route_length"),
                    "mean_turns": _mean_numeric(solved, "turns"),
                    "mean_expanded_nodes": _mean_numeric(solved, "expanded_nodes"),
                    "mean_wall_ms": _mean_numeric(solved, "wall_ms"),
                }
            )
        return {"source": source.name, "rows": output}
