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
from factorio_ai_lab.planning.progression import (
    DEFAULT_ENGINEERING_PLANNER,
    EngineeringState,
)

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


def _process_running(pattern: str) -> bool:
    try:
        completed = subprocess.run(
            ["pgrep", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
            check=False,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
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
    local row={
      name=e.name,
      type=e.type,
      direction=e.direction,
      position={x=e.position.x,y=e.position.y}
    }

    local status=e.status
    if status then
      row.status=tostring(status)
      for key,value in pairs(defines.entity_status) do
        if value==status then
          row.status=key
          break
        end
      end
    end

    local ok_fuel,fuel_inventory=pcall(function()
      return e.get_fuel_inventory()
    end)
    if ok_fuel and fuel_inventory and fuel_inventory.valid then
      local ok_count,count=pcall(function()
        return fuel_inventory.get_item_count("coal")
      end)
      if ok_count then
        row.coal_fuel=count
      end
    end

    local ok_recipe,recipe=pcall(function()
      return e.get_recipe()
    end)
    if ok_recipe and recipe then
      row.recipe=recipe.name
    end

    local ok_energy,energy=pcall(function()
      return e.energy
    end)
    if ok_energy and energy then
      row.energy=energy
    end

    table.insert(entities,row)
  end
end
local production={produced={},consumed={},input={},output={}}
local surface=p.surface
local item_stats=p.force.get_item_production_statistics(surface)
local fluid_stats=p.force.get_fluid_production_statistics(surface)
for name,count in pairs(item_stats.input_counts) do
  if count ~= 0 then
    production.produced[name]=count
    production.output[name]=count
  end
end
for name,count in pairs(item_stats.output_counts) do
  if count ~= 0 then
    production.consumed[name]=count
    production.input[name]=count
  end
end
for name,count in pairs(fluid_stats.input_counts) do
  if count ~= 0 then
    production.produced[name]=count
    production.output[name]=count
  end
end
for name,count in pairs(fluid_stats.output_counts) do
  if count ~= 0 then
    production.consumed[name]=count
    production.input[name]=count
  end
end
rcon.print(helpers.table_to_json({
  connected=true,
  tick=game.tick,
  experiment_tick=storage.elapsed_ticks or 0,
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
        "iron-plate",
        "copper-plate",
        "iron-gear-wheel",
        "copper-cable",
        "electronic-circuit",
        "automation-science-pack",
        "logistic-science-pack",
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

    _RESOURCE_OVERVIEW_COMMAND = r"""
/c local p=storage.agent_characters and storage.agent_characters[1]
if not p then
  rcon.print(helpers.table_to_json({connected=false,error="agent character unavailable"}))
  return
end
local s=p.surface
local radius=192
local cell_size=16
local area={
  left_top={x=p.position.x-radius,y=p.position.y-radius},
  right_bottom={x=p.position.x+radius,y=p.position.y+radius}
}
local cells={}
local totals={}
local nearest={}
local points={}
for _,e in pairs(s.find_entities_filtered{area=area,type="resource"}) do
  if e.valid then
    points[#points+1]={
      name=e.name,
      x=e.position.x,
      y=e.position.y,
      amount=e.amount or 1
    }
    local bx=math.floor(e.position.x/cell_size)
    local by=math.floor(e.position.y/cell_size)
    local key=e.name..":"..bx..":"..by
    local c=cells[key]
    if not c then
      c={
        name=e.name,count=0,amount=0,
        min_x=e.position.x,max_x=e.position.x,
        min_y=e.position.y,max_y=e.position.y,
        sum_x=0,sum_y=0
      }
      cells[key]=c
    end
    c.count=c.count+1
    c.amount=c.amount+(e.amount or 1)
    c.min_x=math.min(c.min_x,e.position.x)
    c.max_x=math.max(c.max_x,e.position.x)
    c.min_y=math.min(c.min_y,e.position.y)
    c.max_y=math.max(c.max_y,e.position.y)
    c.sum_x=c.sum_x+e.position.x
    c.sum_y=c.sum_y+e.position.y

    local t=totals[e.name]
    if not t then
      t={count=0,amount=0}
      totals[e.name]=t
    end
    t.count=t.count+1
    t.amount=t.amount+(e.amount or 1)

    local dx=e.position.x-p.position.x
    local dy=e.position.y-p.position.y
    local d2=dx*dx+dy*dy
    local n=nearest[e.name]
    if not n or d2<n.d2 then
      nearest[e.name]={
        x=e.position.x,y=e.position.y,d2=d2
      }
    end
  end
end
local packed={}
for _,c in pairs(cells) do
  table.insert(packed,{
    name=c.name,
    count=c.count,
    amount=c.amount,
    center={x=c.sum_x/c.count,y=c.sum_y/c.count},
    bounds={
      left_top={x=c.min_x,y=c.min_y},
      right_bottom={x=c.max_x,y=c.max_y}
    }
  })
end
for _,n in pairs(nearest) do
  n.distance=math.sqrt(n.d2)
  n.d2=nil
end
rcon.print(helpers.table_to_json({
  connected=true,
  center={x=p.position.x,y=p.position.y},
  radius=radius,
  cell_size=cell_size,
  cells=packed,
  points=points,
  totals=totals,
  nearest=nearest
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
        self._resource_overview_cache: dict[str, Any] | None = None
        self._resource_overview_cache_at = 0.0

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

    def resource_overview(
        self,
        *,
        max_age_s: float = 15.0,
    ) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if (
                self._resource_overview_cache is not None
                and now - self._resource_overview_cache_at <= max_age_s
            ):
                return self._resource_overview_cache
            try:
                client = self._ensure_client()
                raw = client.send_command(self._RESOURCE_OVERVIEW_COMMAND)
                if not raw:
                    raise RuntimeError("RCON resource overview returned no payload")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise TypeError("RCON resource overview was not an object")
                self._resource_overview_cache = payload
                self._resource_overview_cache_at = now
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
                    "cells": [],
                    "points": [],
                    "totals": {},
                    "nearest": {},
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
                        {"produced": {}, "consumed": {}, "input": {}, "output": {}},
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

    def _research_runner_status(self) -> dict[str, Any]:
        open_play_active = _process_running(
            "factorio_ai_lab.experiments.open_play_runner"
        )
        curriculum_active = _process_running(
            "factorio_ai_lab.experiments.curriculum_runner"
        )
        if open_play_active:
            return {
                "active": True,
                "process": "open_play_runner",
                "arena": "open_play",
            }
        if curriculum_active:
            return {
                "active": True,
                "process": "curriculum_runner",
                "arena": "lab_play",
            }
        return {
            "active": False,
            "process": None,
            "arena": None,
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
            "research_runner": self._research_runner_status(),
        }

    def sample(self) -> dict[str, Any]:
        world = self.factorio.snapshot()
        research = self.research_data()
        resource_overview = self.factorio.resource_overview()
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
            "research": research,
            "progression": self.engineering_progression_data(
                world=world,
                research=research,
            ),
            "resource_overview": resource_overview,
            "evolution": self.evolution_data(research=research),
            "knowledge": self.knowledge_data(),
            "datasets": self.dataset_data(),
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

    def evolution_data(
        self,
        *,
        research: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        research = research if research is not None else self.research_data()
        current = research.get("evolution", {})
        history_path = RUNS_DIR / "evolution_history.jsonl"
        history: list[dict[str, Any]] = []
        if history_path.exists():
            try:
                for raw_line in history_path.read_text(encoding="utf-8").splitlines():
                    if not raw_line.strip():
                        continue
                    row = json.loads(raw_line)
                    if isinstance(row, dict):
                        history.append(row)
            except (OSError, json.JSONDecodeError):
                history = []
        history = history[-16:]

        champion_path = RUNS_DIR / "evolution_champion.json"
        validated_path = RUNS_DIR / "open_play_validated_champion.json"
        champion: dict[str, Any] = {}
        validated: dict[str, Any] = {}
        if champion_path.exists():
            try:
                loaded = json.loads(champion_path.read_text())
                if isinstance(loaded, dict):
                    champion = loaded
            except (OSError, json.JSONDecodeError):
                champion = {}
        if validated_path.exists():
            try:
                loaded = json.loads(validated_path.read_text())
                if isinstance(loaded, dict):
                    validated = loaded
            except (OSError, json.JSONDecodeError):
                validated = {}

        if isinstance(current, dict) and current:
            payload = dict(current)
            if not payload.get("champion") and champion:
                payload["champion"] = champion
            payload["validated_champion"] = validated or None
            payload["history"] = history
            return payload

        run = self.active_run_data()
        generation = int(champion.get("generation", 0) or 0) + 1
        return {
            "scheme": "incumbent_plus_challenger",
            "generation": generation,
            "retention_ratio": 0.80,
            "champion": champion or None,
            "validated_champion": validated or None,
            "history": history,
            "challenger": {
                "run_id": run.get("run_id"),
                "status": (
                    "evaluating"
                    if run.get("status") in {
                        "starting",
                        "running",
                        "learning",
                        "validating",
                    }
                    else "awaiting_selection"
                ),
                "fitness": None,
            },
            "promotion": None,
        }

    def dataset_data(self) -> dict[str, Any]:
        path = RUNS_DIR / "datasets" / "spatial_demonstrations.jsonl"
        if not path.exists():
            return {
                "spatial_demonstrations": 0,
                "training_ready": False,
            }
        try:
            rows = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            return {
                "spatial_demonstrations": 0,
                "training_ready": False,
                "error": "invalid spatial demonstration dataset",
            }
        accepted = sum(
            1
            for row in rows
            if isinstance(row, dict) and bool(row.get("accepted"))
        )
        return {
            "spatial_demonstrations": len(rows),
            "accepted_demonstrations": accepted,
            "training_ready": len(rows) >= 250,
            "minimum_training_target": 250,
        }

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

    def engineering_progression_data(
        self,
        *,
        world: dict[str, Any] | None = None,
        research: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        world = world if world is not None else self.factorio.snapshot()
        research = research if research is not None else self.research_data()

        metrics = research.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        engineering = research.get("engineering_progression", {})
        if not isinstance(engineering, dict):
            engineering = {}

        achieved = {
            str(goal)
            for goal in engineering.get("achieved", [])
            if isinstance(goal, str)
        }
        production = world.get("production", {})
        outputs = (
            production.get("produced", production.get("output", {}))
            if isinstance(production, dict)
            else {}
        )
        if not isinstance(outputs, dict):
            outputs = {}

        if (
            float(outputs.get("iron-plate", 0.0) or 0.0) > 0
            or float(metrics.get("belt_smelting_plate_output", 0.0) or 0.0) > 0
            or float(metrics.get("iron_plate_output", 0.0) or 0.0) > 0
        ):
            achieved.add("iron_backbone")
        if (
            float(outputs.get("coal", 0.0) or 0.0) > 0
            or float(metrics.get("coal_output", 0.0) or 0.0) > 0
        ):
            achieved.add("coal_mining")
        if (
            float(outputs.get("copper-ore", 0.0) or 0.0) > 0
            or float(metrics.get("copper_ore_output", 0.0) or 0.0) > 0
        ):
            achieved.add("copper_mining")
        if (
            float(outputs.get("copper-plate", 0.0) or 0.0) > 0
            or float(metrics.get("copper_plate_output", 0.0) or 0.0) > 0
        ):
            achieved.add("copper_smelting")

        entity_counts: dict[str, int] = {}
        for entity in world.get("entities", []):
            if not isinstance(entity, dict):
                continue
            name = entity.get("name")
            if isinstance(name, str):
                entity_counts[name] = entity_counts.get(name, 0) + 1

        researched = frozenset(
            str(name)
            for name in engineering.get("researched", [])
            if isinstance(name, str)
        )
        stalled_raw = engineering.get("stalled_attempts", {})
        stalled_attempts = (
            {
                str(key): int(value)
                for key, value in stalled_raw.items()
                if isinstance(key, str)
            }
            if isinstance(stalled_raw, dict)
            else {}
        )
        item_signals = {
            str(item): float(value)
            for item, value in outputs.items()
            if isinstance(item, str)
            and isinstance(value, (int, float))
            and float(value) > 0
        }

        state = EngineeringState(
            achieved=frozenset(achieved),
            item_rates=item_signals,
            entity_counts=entity_counts,
            researched=researched,
            stalled_attempts=stalled_attempts,
        )
        inferred = DEFAULT_ENGINEERING_PLANNER.inferred_achieved(state)
        dependency_debt = DEFAULT_ENGINEERING_PLANNER.dependency_debt(state)
        ranked = DEFAULT_ENGINEERING_PLANNER.ranked_frontier(
            EngineeringState(
                achieved=inferred,
                item_rates=item_signals,
                entity_counts=entity_counts,
                researched=researched,
                stalled_attempts=stalled_attempts,
            )
        )

        frontier = [
            {
                "goal_id": candidate.goal.goal_id,
                "label": candidate.goal.label,
                "kind": candidate.goal.kind,
                "score": round(candidate.score, 4),
                "novelty": round(candidate.novelty, 4),
                "retry_penalty": round(candidate.retry_penalty, 4),
            }
            for candidate in ranked
        ]
        return {
            "achieved": sorted(inferred),
            "next_goal": frontier[0] if frontier else None,
            "frontier": frontier,
            "dependency_debt": list(dependency_debt),
            "stalled_attempts": stalled_attempts,
            "terminal": not frontier,
        }

    def render_world_frame(self, mode: str = "game") -> bytes:
        world = self.factorio.snapshot()
        map_context = self.factorio.map_snapshot()
        resource_overview = self.factorio.resource_overview()
        run = self.active_run_data()
        return self.renderer.render(
            world,
            run,
            map_context,
            resource_overview=resource_overview,
            mode=mode,
        )

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
