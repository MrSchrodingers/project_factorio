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
from typing import Any, ClassVar

from factorio_ai_lab.dashboard.rendering import WorldFrameRenderer

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
    """Strictly read-only RCON observer; never constructs FactorioInstance."""

    _SNAPSHOT_COMMAND = r"""
/c local p=storage.agent_characters and storage.agent_characters[1]
if not p then
  rcon.print(helpers.table_to_json({connected=false,error="agent character unavailable"}))
  return
end
local entities={}
for _,e in pairs(p.surface.find_entities_filtered{force=p.force}) do
  if e.valid then
    table.insert(entities,{
      name=e.name,
      type=e.type,
      direction=e.direction,
      position={x=e.position.x,y=e.position.y}
    })
  end
end
local production={input={},output={}}
local surface=p.surface
local item_stats=p.force.get_item_production_statistics(surface)
local fluid_stats=p.force.get_fluid_production_statistics(surface)
for name,count in pairs(item_stats.input_counts) do
  if count ~= 0 then production.output[name]=count end
end
for name,count in pairs(item_stats.output_counts) do
  if count ~= 0 then production.input[name]=count end
end
for name,count in pairs(fluid_stats.input_counts) do
  if count ~= 0 then production.output[name]=count end
end
for name,count in pairs(fluid_stats.output_counts) do
  if count ~= 0 then production.input[name]=count end
end
rcon.print(helpers.table_to_json({
  connected=true,
  tick=storage.elapsed_ticks or game.tick,
  entities=entities,
  production=production
}))
"""

    _PRODUCTION_ITEMS = (
        "iron-ore",
        "copper-ore",
        "coal",
        "stone",
        "uranium-ore",
    )

    _PRODUCTION_PRECISIONS: ClassVar[dict[str, tuple[str, float]]] = {
        "5s": ("five_seconds", 5.0),
        "1m": ("one_minute", 60.0),
        "10m": ("ten_minutes", 600.0),
        "1h": ("one_hour", 3600.0),
        "10h": ("ten_hours", 36000.0),
        "50h": ("fifty_hours", 180000.0),
        "250h": ("two_hundred_fifty_hours", 900000.0),
    }

    _MAP_COMMAND = r"""
/c local p=storage.agent_characters and storage.agent_characters[1]
if not p then
  rcon.print(helpers.table_to_json({connected=false,error="agent character unavailable"}))
  return
end
local s=p.surface
local radius=42
local area={
  left_top={x=p.position.x-radius,y=p.position.y-radius},
  right_bottom={x=p.position.x+radius,y=p.position.y+radius}
}
local resources={}
for _,e in pairs(s.find_entities_filtered{area=area,type="resource"}) do
  if e.valid then
    table.insert(resources,{
      name=e.name,
      amount=e.amount or 1,
      position={x=e.position.x,y=e.position.y}
    })
  end
end
local natural={}
local natural_types={"tree","simple-entity"}
for _,kind in pairs(natural_types) do
  for _,e in pairs(s.find_entities_filtered{area=area,type=kind}) do
    if e.valid and #natural < 900 then
      table.insert(natural,{
        name=e.name,
        direction=e.direction or 0,
        position={x=e.position.x,y=e.position.y}
      })
    end
  end
end
local water={}
local water_names={
  "water","deepwater","water-green","deepwater-green",
  "water-shallow","water-mud"
}
for _,tile in pairs(s.find_tiles_filtered{area=area,name=water_names}) do
  table.insert(water,{x=tile.position.x,y=tile.position.y})
end
rcon.print(helpers.table_to_json({
  connected=true,
  center={x=p.position.x,y=p.position.y},
  radius=radius,
  resources=resources,
  natural=natural,
  water_tiles=water
}))
"""

    def __init__(self, host: str = "127.0.0.1", port: int = 27000) -> None:
        self.host = host
        self.port = port
        self._client: Any | None = None
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._map_cache: dict[str, Any] | None = None
        self._map_cache_at = 0.0
        self._map_cache_center: tuple[float, float] | None = None
        self._production_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def connected(self) -> bool:
        return _port_open(self.host, self.port)

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                close = getattr(self._client, "close", None)
                if callable(close):
                    try:
                        close()
                    except OSError:
                        pass
                self._client = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        from factorio_rcon import RCONClient

        self._client = RCONClient(self.host, self.port, "factorio")
        return self._client

    def map_snapshot(
        self,
        *,
        max_age_s: float = 5.0,
    ) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if (
                self._map_cache is not None
                and now - self._map_cache_at <= max_age_s
            ):
                return self._map_cache

            try:
                client = self._ensure_client()
                raw = client.send_command(self._MAP_COMMAND)
                if not raw:
                    raise RuntimeError("RCON map snapshot returned no payload")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise TypeError("RCON map snapshot was not an object")
                center = payload.get("center")
                if isinstance(center, dict):
                    try:
                        self._map_cache_center = (
                            float(center["x"]),
                            float(center["y"]),
                        )
                    except (KeyError, TypeError, ValueError):
                        self._map_cache_center = None
                self._map_cache = payload
                self._map_cache_at = now
                return payload
            except (
                OSError,
                RuntimeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                return {
                    "connected": False,
                    "resources": [],
                    "natural": [],
                    "water_tiles": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }

    def production_statistics(
        self,
        precision_key: str = "1m",
        *,
        max_age_s: float = 2.5,
    ) -> dict[str, Any]:
        if precision_key not in self._PRODUCTION_PRECISIONS:
            raise ValueError(
                f"unsupported production precision: {precision_key}"
            )

        now = time.monotonic()
        cached = self._production_cache.get(precision_key)
        if cached is not None and now - cached[0] <= max_age_s:
            return cached[1]

        precision_name, duration_seconds = self._PRODUCTION_PRECISIONS[
            precision_key
        ]
        names = ",".join(f'"{name}"' for name in self._PRODUCTION_ITEMS)
        command = f"""
/c local p=storage.agent_characters and storage.agent_characters[1]
if not p then
  rcon.print(helpers.table_to_json({{connected=false,error="agent character unavailable"}}))
  return
end
local stats=p.force.get_item_production_statistics(p.surface)
local precision=defines.flow_precision_index.{precision_name}
local names={{{names}}}
local series={{}}
for _,name in ipairs(names) do
  local produced={{}}
  local consumed={{}}
  for i=300,1,-1 do
    produced[#produced+1]=stats.get_flow_count{{
      name=name,category="input",precision_index=precision,sample_index=i
    }}
    consumed[#consumed+1]=stats.get_flow_count{{
      name=name,category="output",precision_index=precision,sample_index=i
    }}
  end
  series[name]={{
    produced_rate=stats.get_flow_count{{
      name=name,category="input",precision_index=precision
    }},
    produced_count=stats.get_flow_count{{
      name=name,category="input",precision_index=precision,count=true
    }},
    consumed_rate=stats.get_flow_count{{
      name=name,category="output",precision_index=precision
    }},
    consumed_count=stats.get_flow_count{{
      name=name,category="output",precision_index=precision,count=true
    }},
    produced=produced,
    consumed=consumed
  }}
end
rcon.print(helpers.table_to_json({{
  connected=true,
  precision="{precision_key}",
  duration_seconds={duration_seconds},
  sample_count=300,
  series=series
}}))
"""

        with self._lock:
            try:
                client = self._ensure_client()
                raw = client.send_command(command)
                if not raw:
                    raise RuntimeError(
                        "RCON production statistics returned no payload"
                    )
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise TypeError(
                        "RCON production statistics were not an object"
                    )
                payload["sample_period_seconds"] = round(
                    duration_seconds / 300.0,
                    6,
                )
                self._production_cache[precision_key] = (now, payload)
                return payload
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                self._client = None
                return {
                    "connected": False,
                    "precision": precision_key,
                    "duration_seconds": duration_seconds,
                    "sample_count": 300,
                    "sample_period_seconds": round(
                        duration_seconds / 300.0,
                        6,
                    ),
                    "series": {},
                    "error": f"{type(exc).__name__}: {exc}",
                }

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
                client = self._ensure_client()
                raw = client.send_command(self._SNAPSHOT_COMMAND)
                if not raw:
                    raise RuntimeError("RCON snapshot returned no payload")
                payload = json.loads(raw)
                entities = payload.get("entities", [])
                if not isinstance(entities, list):
                    entities = []
                self._last_error = None
                return {
                    "connected": bool(payload.get("connected", True)),
                    "tick": payload.get("tick"),
                    "entities": entities,
                    "entity_count": len(entities),
                    "production": payload.get(
                        "production",
                        {"input": {}, "output": {}},
                    ),
                    "latency_ms": round(
                        (time.perf_counter() - started) * 1000.0,
                        2,
                    ),
                    "error": payload.get("error"),
                }
            except (
                OSError,
                RuntimeError,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._client = None
                return {
                    "connected": False,
                    "entities": [],
                    "entity_count": 0,
                    "latency_ms": round(
                        (time.perf_counter() - started) * 1000.0,
                        2,
                    ),
                    "error": self._last_error,
                }


class DashboardState:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.config = RuntimeConfigStore()
        self.factorio = FactorioObserver()
        self.renderer = WorldFrameRenderer()
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
            "render": self.renderer.status(),
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
            "run": self.active_run_data(),
            "research": self.research_data(),
            "knowledge": self.knowledge_data(),
        }

    def history_data(self) -> list[dict[str, Any]]:
        with self.history_lock:
            return list(self.history)

    def active_run_data(self) -> dict[str, Any]:
        path = RUNS_DIR / "active_run.json"
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def research_data(self) -> dict[str, Any]:
        path = RUNS_DIR / "research_state.json"
        if not path.exists():
            return {
                "status": "idle",
                "stage": None,
                "curriculum": [],
                "online_learning": {},
            }
        try:
            loaded = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"status": "degraded", "error": "invalid research_state.json"}
        return loaded if isinstance(loaded, dict) else {}

    def knowledge_data(self, limit: int = 12) -> dict[str, Any]:
        path = RUNS_DIR / "knowledge.jsonl"
        if not path.exists():
            return {"count": 0, "lessons": []}
        try:
            lines = [line for line in path.read_text().splitlines() if line.strip()]
            lessons = [json.loads(line) for line in lines[-limit:]]
        except (OSError, json.JSONDecodeError):
            return {"count": 0, "lessons": [], "error": "invalid knowledge log"}
        return {"count": len(lines), "lessons": lessons}

    def render_world_frame(self) -> bytes:
        world = self.factorio.snapshot()
        map_context = self.factorio.map_snapshot()
        run = self.active_run_data()
        return self.renderer.render(world, run, map_context)

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
