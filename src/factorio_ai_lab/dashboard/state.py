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
from math import isfinite
from pathlib import Path
from typing import Any, ClassVar

from factorio_ai_lab.dashboard.rendering import WorldFrameRenderer
from factorio_ai_lab.learning.autonomy import evaluate_factory_autonomy
from factorio_ai_lab.learning.discovery import (
    Discovery,
    discoveries_from_history,
    summarise,
)
from factorio_ai_lab.learning.factory_graph import build_factory_graph
from factorio_ai_lab.learning.survival_analysis import (
    DISPOSITION_INELIGIBLE,
    DISPOSITION_UNKNOWN,
    SampleGate,
    competing_risks,
    evaluate_sample_gate,
    hazard_table,
    kaplan_meier,
    observations_from_generation_reports,
)
from factorio_ai_lab.learning.telemetry import (
    append_jsonl as append_telemetry_jsonl,
)
from factorio_ai_lab.learning.telemetry import compact_telemetry_sample
from factorio_ai_lab.planning.factorio_catalog import (
    EARLY_GAME_PRODUCTION_PLANNER,
    FACTORIO_DATA_VERSION,
)
from factorio_ai_lab.planning.progression import (
    DEFAULT_ENGINEERING_PLANNER,
    EngineeringState,
)
from factorio_ai_lab.planning.runtime_catalog import RuntimeFactorioCatalog
from factorio_ai_lab.runtime import runtime_status

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNS_DIR = PROJECT_ROOT / "runs"
RUNTIME_CONFIG = RUNS_DIR / "runtime_config.json"
TELEMETRY_LOG = RUNS_DIR / "telemetry" / "world_samples.jsonl"
GENERATION_REPORTS_DIR = RUNS_DIR / "generation_reports"
EVOLUTION_HISTORY_PATH = RUNS_DIR / "evolution_history.jsonl"

#: How many findings the discoveries payload carries at most. The whole set
#: is summarised regardless; the cut is reported next to the list so a
#: truncated view is never mistaken for the whole record.
DISCOVERY_PAYLOAD_LIMIT = 200


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


def _as_list(value: Any) -> list[Any]:
    """Coerce a Lua-serialised collection into a JSON array.

    ``helpers.table_to_json`` renders an empty Lua table as ``{}``, not
    ``[]``, so a viewport with no trees or no ore arrives as an object. The
    browser then throws on ``for...of``, which kills the render loop for good
    because the next frame is never scheduled.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def json_finite(value: Any, depth: int = 0) -> Any:
    """Replace non-finite floats with None so the payload stays valid JSON.

    ``json.dumps`` emits the bare tokens ``Infinity``/``NaN`` by default, and
    Starlette's ``WebSocket.send_json`` uses that default. Those tokens are
    rejected by ``JSON.parse`` (RFC 8259 has no non-finite literal), so a
    single infinite route cost silently kills every live dashboard update.
    """
    if depth > 12:
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_finite(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [json_finite(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return [json_finite(item, depth + 1) for item in value]
    return value


def _json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)
    if isinstance(value, float) and not isfinite(value):
        return None
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


SURVIVAL_EXCLUSION_UNREADABLE = "unreadable"


def _generation_reports_fingerprint(directory: Path) -> tuple[tuple[str, int, int], ...]:
    """Identity of the report set on disk, used to invalidate the survival cache.

    A clock-based cache would serve a stale curve as if it were current: the
    evolution loop appends a generation report roughly every 17 minutes, and
    watching that move is the reason the panel exists. The fingerprint covers
    the name, size and modification time of every report, so both a new report
    and a rewritten one force a reread.
    """
    entries: list[tuple[str, int, int]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((path.name, stat.st_size, stat.st_mtime_ns))
    return tuple(entries)


def _read_generation_reports(
    directory: Path,
) -> tuple[list[dict[str, Any]], int, int, float | None]:
    """Read the generation reports, counting the ones that could not be read.

    Returns the decoded reports, how many files were on disk, how many were
    unreadable, and the newest modification time. An unreadable report is
    counted rather than skipped in silence: "no failure recorded" and "the
    record could not be read" are different states and must not collapse.
    """
    reports: list[dict[str, Any]] = []
    unreadable = 0
    newest: float | None = None
    paths = sorted(directory.glob("*.json"))
    for path in paths:
        try:
            stat = path.stat()
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            unreadable += 1
            continue
        if not isinstance(loaded, dict):
            unreadable += 1
            continue
        newest = stat.st_mtime if newest is None else max(newest, stat.st_mtime)
        reports.append(loaded)
    return reports, len(paths), unreadable, newest


def _survival_gate_payload(gate: SampleGate) -> dict[str, Any]:
    """The sample-size verdict, whole, so it travels with every number it qualifies."""
    return {
        "n": gate.n,
        "n_events": gate.n_events,
        "n_censored": gate.n_censored,
        "distinct_event_times": gate.distinct_event_times,
        "purpose": gate.purpose,
        "min_events_required": gate.min_events_required,
        "sufficient": gate.sufficient,
        "verdict": gate.verdict,
        "reason": gate.reason,
    }


def _survival_report_payload(directory: Path) -> dict[str, Any]:
    """Survival across generations, or a declared absence of measurement.

    Absence is never rendered as a curve. A missing directory, an unreadable
    report or a generation that predates the instrumentation leaves ``curve``,
    ``hazard`` and ``competing_risks`` null, with ``measured`` false and a
    reason, because an empty curve on screen reads as a measured zero.

    Every estimate carries its own sample gate alongside the sample-wide one:
    a client that renders only the curve still has the verdict that qualifies
    it.
    """
    payload: dict[str, Any] = {
        "generated_at": time.time(),
        "measured": False,
        "reason": "",
        "source": {
            "directory": str(directory),
            "exists": directory.is_dir(),
            "reports_found": 0,
            "reports_read": 0,
            "newest_report_at": None,
        },
        "excluded": {
            DISPOSITION_UNKNOWN: 0,
            DISPOSITION_INELIGIBLE: 0,
            "no_runtime": 0,
            SURVIVAL_EXCLUSION_UNREADABLE: 0,
        },
        "sample": _survival_gate_payload(evaluate_sample_gate(())),
        "curve": None,
        "hazard": None,
        "competing_risks": None,
    }
    if not payload["source"]["exists"]:
        payload["reason"] = (
            f"no generation report directory at {directory}: survival is not measured"
        )
        return payload

    reports, found, unreadable, newest = _read_generation_reports(directory)
    payload["source"]["reports_found"] = found
    payload["source"]["reports_read"] = len(reports)
    payload["source"]["newest_report_at"] = newest
    payload["excluded"][SURVIVAL_EXCLUSION_UNREADABLE] = unreadable
    if not found:
        payload["reason"] = "no generation report on disk: survival is not measured"
        return payload

    extraction = observations_from_generation_reports(reports)
    for reason_key, count in extraction.excluded.items():
        payload["excluded"][reason_key] = payload["excluded"].get(reason_key, 0) + int(count)
    payload["sample"] = _survival_gate_payload(extraction.gate)
    if not extraction.observations:
        excluded = payload["excluded"]
        payload["reason"] = (
            f"{found} report(s) on disk and no lifetime among them "
            f"({excluded[DISPOSITION_UNKNOWN]} without a recognised halt cause, "
            f"{excluded[DISPOSITION_INELIGIBLE]} with no factory built, "
            f"{excluded['no_runtime']} without a productive runtime, "
            f"{excluded[SURVIVAL_EXCLUSION_UNREADABLE]} unreadable): "
            "survival is not measured"
        )
        return payload

    curve = kaplan_meier(extraction.observations)
    hazard = hazard_table(extraction.observations)
    risks = competing_risks(extraction.observations)
    payload["measured"] = True
    payload["reason"] = (
        f"{len(extraction.observations)} lifetime(s) extracted from {found} report(s); "
        "the sample gate qualifies every number below"
    )
    payload["curve"] = {
        "confidence_level": curve.confidence_level,
        "follow_up_end_s": curve.follow_up_end_s,
        "median_survival_s": curve.median_survival_s,
        "median_survival_reason": curve.median_survival_reason,
        "gate": _survival_gate_payload(curve.gate),
        "points": [_json_safe(point) for point in curve.points],
    }
    payload["hazard"] = {
        "gate": _survival_gate_payload(hazard.gate),
        "points": [_json_safe(point) for point in hazard.points],
    }
    payload["competing_risks"] = {
        "gate": _survival_gate_payload(risks.gate),
        "cause_counts": dict(risks.cause_counts),
        "incidence": {
            cause: [_json_safe(point) for point in points]
            for cause, points in risks.incidence.items()
        },
    }
    return payload


def _evolution_history_fingerprint(path: Path) -> tuple[int, int] | None:
    """Identity of the history file, used to invalidate the discoveries cache.

    The loop appends one generation every ~17 minutes, so a clock-based cache
    would keep serving findings from before the generation being watched.
    Size and modification time together catch both an append and a rewrite.
    None means the file is not there, which is itself a state worth caching.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


def _read_evolution_history(path: Path) -> tuple[list[dict[str, Any]], int, int, bool]:
    """Read the history rows, counting the lines that could not be read.

    Returns the decoded rows, how many non-empty lines were on disk, how many
    of them were unreadable, and whether the file itself could be opened at
    all. A malformed line is counted and skipped rather than aborting the
    read: the loop writes the file while it is being served, so a half-written
    last line is expected, not exceptional. A file that cannot be opened
    reports zero lines, and that zero must not be read as an empty history.
    """
    rows: list[dict[str, Any]] = []
    found = 0
    unreadable = 0
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], 0, 0, False
    for line in raw.splitlines():
        if not line.strip():
            continue
        found += 1
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        if not isinstance(loaded, dict):
            unreadable += 1
            continue
        rows.append(loaded)
    return rows, found, unreadable, True


def _discovery_payload(path: Path, *, limit: int) -> dict[str, Any]:
    """What each generation found, or a declared absence of a record to read.

    Absence is never rendered as a summary of zeros. A missing file, an empty
    one and one whose every line is unreadable leave ``summary`` null with
    ``measured`` false and a reason, because "nothing was found" and "there was
    nothing to read" are different statements.

    ``retained`` keeps the three states the record carries: promoted, rejected,
    and no verdict written. The third is not the second.

    The summary covers every finding; ``discoveries`` carries at most ``limit``
    of them, most recent first, and the payload states the total and whether it
    was cut.
    """
    payload: dict[str, Any] = {
        "generated_at": time.time(),
        "measured": False,
        "reason": "",
        "source": {
            "path": str(path),
            "exists": path.is_file(),
            "lines_found": 0,
            "lines_read": 0,
            "lines_unreadable": 0,
            "readable": None,
            "modified_at": None,
        },
        "summary": None,
        "discoveries": [],
        "total": 0,
        "returned": 0,
        "limit": limit,
        "truncated": False,
    }
    if not payload["source"]["exists"]:
        payload["reason"] = (
            f"no evolution history at {path}: what the search found is not recorded"
        )
        return payload

    try:
        payload["source"]["modified_at"] = path.stat().st_mtime
    except OSError:
        payload["source"]["modified_at"] = None

    rows, found, unreadable, readable = _read_evolution_history(path)
    payload["source"]["lines_found"] = found
    payload["source"]["lines_read"] = len(rows)
    payload["source"]["lines_unreadable"] = unreadable
    payload["source"]["readable"] = readable
    if not rows:
        if not readable:
            payload["reason"] = (
                f"evolution history at {path} exists but could not be read: "
                "what the search found is not recorded"
            )
        elif not found:
            payload["reason"] = (
                f"evolution history at {path} is empty: "
                "what the search found is not recorded"
            )
        else:
            payload["reason"] = (
                f"evolution history at {path} has {found} line(s) and none could be read "
                f"({unreadable} unreadable): what the search found is not recorded"
            )
        return payload

    found_items: tuple[Discovery, ...] = discoveries_from_history(rows)
    ordered = [item.to_dict() for item in reversed(found_items)]
    shown = ordered[:limit] if limit >= 0 else ordered

    payload["measured"] = True
    payload["summary"] = summarise(found_items)
    payload["discoveries"] = shown
    payload["total"] = len(ordered)
    payload["returned"] = len(shown)
    payload["truncated"] = len(shown) < len(ordered)
    payload["reason"] = (
        f"{len(found_items)} finding(s) recovered from {len(rows)} generation record(s) "
        f"({unreadable} unreadable line(s)); "
        + (
            f"showing the {len(shown)} most recent"
            if payload["truncated"]
            else "showing all of them"
        )
    )
    return payload


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
      unit_number=e.unit_number,
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
local min_x=p.position.x
local max_x=p.position.x
local min_y=p.position.y
local max_y=p.position.y
for _,e in pairs(s.find_entities_filtered{force=p.force}) do
  if e.valid then
    min_x=math.min(min_x,e.position.x)
    max_x=math.max(max_x,e.position.x)
    min_y=math.min(min_y,e.position.y)
    max_y=math.max(max_y,e.position.y)
  end
end
local margin=18
local left=min_x-margin
local right=max_x+margin
local top=min_y-margin
local bottom=max_y+margin
local max_span=220
if right-left>max_span then
  local cx=(left+right)/2
  left=cx-max_span/2
  right=cx+max_span/2
end
if bottom-top>max_span then
  local cy=(top+bottom)/2
  top=cy-max_span/2
  bottom=cy+max_span/2
end
local area={
  left_top={x=left,y=top},
  right_bottom={x=right,y=bottom}
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
local terrain_names={
  "water","deepwater","water-green","deepwater-green",
  "water-shallow","water-mud",
  "stone-path","concrete","refined-concrete",
  "hazard-concrete-left","hazard-concrete-right",
  "refined-hazard-concrete-left","refined-hazard-concrete-right"
}
local terrain_rows={}
local terrain_tile_count=0
local water_tile_count=0
for _,tile in pairs(s.find_tiles_filtered{area=area,name=terrain_names}) do
  terrain_tile_count=terrain_tile_count+1
  if string.find(tile.name,"water",1,true) then
    water_tile_count=water_tile_count+1
  end
  local y=tile.position.y
  local key=tile.name..":"..tostring(y)
  local row=terrain_rows[key]
  if not row then
    row={name=tile.name,y=y,xs={}}
    terrain_rows[key]=row
  end
  row.xs[#row.xs+1]=tile.position.x
end
local terrain_runs={}
for _,row in pairs(terrain_rows) do
  table.sort(row.xs)
  local first=nil
  local previous=nil
  for _,x in ipairs(row.xs) do
    if first==nil then
      first=x
      previous=x
    elseif x==previous+1 then
      previous=x
    else
      terrain_runs[#terrain_runs+1]={
        name=row.name,y=row.y,x1=first,x2=previous
      }
      first=x
      previous=x
    end
  end
  if first~=nil then
    terrain_runs[#terrain_runs+1]={
      name=row.name,y=row.y,x1=first,x2=previous
    }
  end
end
rcon.print(helpers.table_to_json({
  connected=true,
  center={x=p.position.x,y=p.position.y},
  bounds={
    left_top={x=left,y=top},
    right_bottom={x=right,y=bottom}
  },
  resources=resources,
  natural=natural,
  terrain_runs=terrain_runs,
  terrain_tile_count=terrain_tile_count,
  water_tile_count=water_tile_count
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

    _GAME_KNOWLEDGE_COMMAND = r"""
/c local p=storage.agent_characters and storage.agent_characters[1]
if not p then
  rcon.print(helpers.table_to_json({connected=false,error="agent character unavailable"}))
  return
end

local function names_from_dictionary(values)
  local out={}
  if values then
    for name,_ in pairs(values) do out[#out+1]=name end
  end
  table.sort(out)
  return out
end

local function string_array(values)
  local out={}
  if values then
    for key,value in pairs(values) do
      if type(key)=="string" then
        out[#out+1]=key
      elseif type(value)=="string" then
        out[#out+1]=value
      end
    end
  end
  table.sort(out)
  return out
end

local function recipe_categories(recipe)
  local ok_categories,categories=pcall(function()
    return recipe.categories
  end)
  if ok_categories and categories then
    return string_array(categories)
  end
  local ok_category,category=pcall(function()
    return recipe.category
  end)
  if ok_category and category then
    return {category}
  end
  return {"crafting"}
end

local function ingredient_rows(values)
  local out={}
  if values then
    for _,value in pairs(values) do
      out[#out+1]={
        name=value.name,
        type=value.type,
        amount=value.amount or 0
      }
    end
  end
  return out
end

local function product_rows(values)
  local out={}
  if values then
    for _,value in pairs(values) do
      local amount=value.amount
      if not amount then
        local amin=value.amount_min or 0
        local amax=value.amount_max or amin
        amount=(amin+amax)/2
      end
      amount=amount*(value.probability or 1)
      out[#out+1]={
        name=value.name,
        type=value.type,
        amount=amount
      }
    end
  end
  return out
end

local recipes={}
for name,recipe in pairs(prototypes.recipe) do
  local force_recipe=p.force.recipes[name]
  local categories={}
  local ok_categories,raw_categories=pcall(function()
    return recipe.categories
  end)
  if ok_categories and raw_categories then
    categories=string_array(raw_categories)
  else
    local ok_category,category=pcall(function()
      return recipe.category
    end)
    if ok_category and category then categories={tostring(category)} end
  end
  recipes[#recipes+1]={
    name=name,
    energy=recipe.energy,
    categories=recipe_categories(recipe),
    ingredients=ingredient_rows(recipe.ingredients),
    products=product_rows(recipe.products),
    enabled_by_default=recipe.enabled,
    enabled=force_recipe and force_recipe.enabled or false,
    hidden_from_player_crafting=recipe.hidden_from_player_crafting
  }
end
table.sort(recipes,function(a,b) return a.name<b.name end)

local technologies={}
for name,technology in pairs(prototypes.technology) do
  local force_technology=p.force.technologies[name]
  local unlocks={}
  for _,effect in pairs(technology.effects or {}) do
    if effect.type=="unlock-recipe" and effect.recipe then
      unlocks[#unlocks+1]=effect.recipe
    end
  end
  table.sort(unlocks)
  technologies[#technologies+1]={
    name=name,
    prerequisites=names_from_dictionary(technology.prerequisites),
    unlocks=unlocks,
    research_unit_ingredients=ingredient_rows(
      technology.research_unit_ingredients
    ),
    researched=force_technology and force_technology.researched or false,
    enabled=force_technology and force_technology.enabled or false
  }
end
table.sort(technologies,function(a,b) return a.name<b.name end)

local machines={}
for name,entity in pairs(prototypes.entity) do
  local ok_categories,categories=pcall(function()
    return entity.crafting_categories
  end)
  local ok_speed,speed=pcall(function()
    return entity.crafting_speed
  end)
  local ok_resources,resource_categories=pcall(function()
    return entity.resource_categories
  end)
  local ok_mining,mining_speed=pcall(function()
    return entity.mining_speed
  end)
  local crafting=ok_categories and categories and next(categories)~=nil
  local mining=ok_resources and resource_categories and next(resource_categories)~=nil
  if crafting or mining then
    machines[#machines+1]={
      name=name,
      type=entity.type,
      crafting_categories=crafting and string_array(categories) or {},
      crafting_speed=ok_speed and speed or nil,
      resource_categories=mining and string_array(resource_categories) or {},
      mining_speed=ok_mining and mining_speed or nil
    }
  end
end
table.sort(machines,function(a,b) return a.name<b.name end)

rcon.print(helpers.table_to_json({
  connected=true,
  factorio_version=script.active_mods.base or "base",
  recipes=recipes,
  technologies=technologies,
  machines=machines,
  counts={
    recipes=#recipes,
    technologies=#technologies,
    machines=#machines
  }
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
        self._map_view_cache: dict[
            tuple[float, float, float],
            tuple[float, dict[str, Any]],
        ] = {}
        self._production_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._resource_overview_cache: dict[str, Any] | None = None
        self._resource_overview_cache_at = 0.0
        self._game_knowledge_cache: dict[str, Any] | None = None
        self._game_knowledge_cache_at = 0.0
        self._entity_prototype_cache: dict[str, Any] | None = None
        self._entity_prototype_cache_at = 0.0

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
        center_x: float | None = None,
        center_y: float | None = None,
        radius: float | None = None,
    ) -> dict[str, Any]:
        now = time.monotonic()
        explicit_view = (
            center_x is not None
            and center_y is not None
            and radius is not None
        )
        view_key: tuple[float, float, float] | None = None
        command = self._MAP_COMMAND

        if explicit_view:
            cx = float(center_x)
            cy = float(center_y)
            view_radius = min(96.0, max(6.0, float(radius)))
            view_key = (
                round(cx, 2),
                round(cy, 2),
                round(view_radius, 2),
            )
            cached = self._map_view_cache.get(view_key)
            if cached is not None and now - cached[0] <= max_age_s:
                return cached[1]

            auto_block = """local min_x=p.position.x
local max_x=p.position.x
local min_y=p.position.y
local max_y=p.position.y
for _,e in pairs(s.find_entities_filtered{force=p.force}) do
  if e.valid then
    min_x=math.min(min_x,e.position.x)
    max_x=math.max(max_x,e.position.x)
    min_y=math.min(min_y,e.position.y)
    max_y=math.max(max_y,e.position.y)
  end
end
local margin=18
local left=min_x-margin
local right=max_x+margin
local top=min_y-margin
local bottom=max_y+margin
local max_span=220
if right-left>max_span then
  local cx=(left+right)/2
  left=cx-max_span/2
  right=cx+max_span/2
end
if bottom-top>max_span then
  local cy=(top+bottom)/2
  top=cy-max_span/2
  bottom=cy+max_span/2
end
"""
            view_block = f"""local viewport_cx={cx:.6f}
local viewport_cy={cy:.6f}
local viewport_radius={view_radius:.6f}
local left=viewport_cx-viewport_radius
local right=viewport_cx+viewport_radius
local top=viewport_cy-viewport_radius
local bottom=viewport_cy+viewport_radius
"""
            if auto_block not in command:
                raise RuntimeError("map command viewport block not found")
            command = command.replace(auto_block, view_block, 1)
            command = command.replace(
                "center={x=p.position.x,y=p.position.y},",
                (
                    "center={x=viewport_cx,y=viewport_cy},"
                    "viewport={center={x=viewport_cx,y=viewport_cy},"
                    "radius=viewport_radius},"
                ),
                1,
            )

        with self._lock:
            if (
                not explicit_view
                and self._map_cache is not None
                and now - self._map_cache_at <= max_age_s
            ):
                return self._map_cache

            try:
                client = self._ensure_client()
                raw = client.send_command(command)
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
                if explicit_view and view_key is not None:
                    self._map_view_cache[view_key] = (now, payload)
                    if len(self._map_view_cache) > 20:
                        oldest = min(
                            self._map_view_cache,
                            key=lambda key: self._map_view_cache[key][0],
                        )
                        self._map_view_cache.pop(oldest, None)
                else:
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
                    "terrain_runs": [],
                    "terrain_tile_count": 0,
                    "water_tile_count": 0,
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

    def game_knowledge(
        self,
        *,
        max_age_s: float = 300.0,
    ) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            if (
                self._game_knowledge_cache is not None
                and now - self._game_knowledge_cache_at <= max_age_s
            ):
                return self._game_knowledge_cache
            try:
                client = self._ensure_client()
                raw = client.send_command(self._GAME_KNOWLEDGE_COMMAND)
                if not raw:
                    raise RuntimeError("RCON game knowledge returned no payload")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise TypeError("RCON game knowledge was not an object")
                self._game_knowledge_cache = payload
                self._game_knowledge_cache_at = now
                try:
                    knowledge_path = RUNS_DIR / "game_knowledge_graph.json"
                    temporary = knowledge_path.with_suffix(".json.tmp")
                    temporary.write_text(
                        json.dumps(
                            payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    temporary.replace(knowledge_path)
                except OSError:
                    pass
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
                    "recipes": [],
                    "technologies": [],
                    "machines": [],
                    "counts": {
                        "recipes": 0,
                        "technologies": 0,
                        "machines": 0,
                    },
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

    _ENTITY_PROTOTYPE_COMMAND = r"""/c local rows={}
for name,e in pairs(prototypes.entity) do
  local ok,items=pcall(function() return e.items_to_place_this end)
  if ok and items and next(items)~=nil then
    local sb=e.selection_box
    local cb=e.collision_box
    rows[#rows+1]={
      name=name,
      type=e.type,
      tile_width=e.tile_width,
      tile_height=e.tile_height,
      selection_box={sb.left_top.x,sb.left_top.y,sb.right_bottom.x,sb.right_bottom.y},
      collision_box={cb.left_top.x,cb.left_top.y,cb.right_bottom.x,cb.right_bottom.y}
    }
  end
end
table.sort(rows,function(a,b) return a.name<b.name end)
rcon.print(helpers.table_to_json({connected=true,count=#rows,prototypes=rows}))
"""

    def entity_prototypes(
        self,
        *,
        max_age_s: float = 900.0,
    ) -> dict[str, Any]:
        """Footprint metadata for every player-placeable entity.

        The client needs ``tile_width``/``tile_height`` and the selection box
        to seat a sprite on the exact tiles the entity occupies. Without it a
        sprite can only be centred on the position, which is what makes the
        current map look misaligned.
        """
        now = time.monotonic()
        with self._lock:
            if (
                self._entity_prototype_cache is not None
                and now - self._entity_prototype_cache_at <= max_age_s
            ):
                return self._entity_prototype_cache
            try:
                client = self._ensure_client()
                raw = client.send_command(self._ENTITY_PROTOTYPE_COMMAND)
                if not raw:
                    raise RuntimeError("RCON entity prototypes returned no payload")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise TypeError("RCON entity prototypes were not an object")
                by_name: dict[str, Any] = {}
                for row in payload.get("prototypes", []):
                    if isinstance(row, dict) and isinstance(row.get("name"), str):
                        by_name[row["name"]] = row
                payload["by_name"] = by_name
                self._entity_prototype_cache = payload
                self._entity_prototype_cache_at = now
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
                    "count": 0,
                    "prototypes": [],
                    "by_name": {},
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
        self._last_telemetry_write = 0.0
        self._frame_cache: dict[str, tuple[float, bytes]] = {}
        self._frame_cache_lock = threading.Lock()
        self._artifact_cache: dict[str, tuple[float, Any]] = {}
        self.generation_reports_dir = GENERATION_REPORTS_DIR
        self._survival_cache: dict[
            str,
            tuple[tuple[tuple[str, int, int], ...], dict[str, Any]],
        ] = {}
        self.evolution_history_path = EVOLUTION_HISTORY_PATH
        self._discovery_cache: dict[
            str,
            tuple[tuple[int, int] | None, dict[str, Any]],
        ] = {}

    def close(self) -> None:
        self.factorio.close()

    def _cached_artifact(
        self,
        key: str,
        *,
        ttl_s: float,
        loader: Any,
    ) -> Any:
        now = time.time()
        cached = self._artifact_cache.get(key)
        if cached is not None and now - cached[0] <= ttl_s:
            return cached[1]
        value = loader()
        self._artifact_cache[key] = (now, value)
        return value

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
        execution = runtime_status()
        if execution.get("writer_active"):
            action = execution.get("action", {})
            action = action if isinstance(action, dict) else {}
            lease = execution.get("lease", {})
            lease = lease if isinstance(lease, dict) else {}
            arena = str(
                action.get("arena")
                or lease.get("arena")
                or "factorio"
            )
            phase = (
                "open_play_validation"
                if arena == "open_play"
                else "lab_generation"
                if arena == "lab_play"
                else "factorio_execution"
            )
            return {
                "active": True,
                "process": str(
                    lease.get("owner")
                    or action.get("owner")
                    or "factorio_world_writer"
                ),
                "arena": arena,
                "phase": phase,
                "model_training": False,
                "heartbeat": {
                    "action_active": execution.get("action_active", False),
                    "heartbeat_age_s": execution.get("heartbeat_age_s"),
                    "action_id": action.get("action_id"),
                    "action_kind": action.get("action_kind"),
                    "stage": action.get("stage"),
                    "elapsed_s": action.get("elapsed_s"),
                },
            }

        evolution_loop_active = _process_running(
            "factorio_ai_lab.experiments.evolution_loop"
        )
        open_play_active = _process_running(
            "factorio_ai_lab.experiments.open_play_runner"
        )
        curriculum_active = _process_running(
            "factorio_ai_lab.experiments.curriculum_runner"
        )
        model_training_active = (
            _process_running(
                "factorio_ai_lab.experiments.train_recurrent_world_model"
            )
            or _process_running(
                "factorio_ai_lab.experiments.train_spatial_policy"
            )
        )

        if evolution_loop_active:
            if model_training_active:
                phase = "model_training"
            elif open_play_active:
                phase = "open_play_validation"
            else:
                research = self.research_data()
                research_status = str(research.get("status", ""))
                if research_status in {
                    "starting",
                    "running",
                    "learning",
                    "validating",
                }:
                    phase = "lab_generation"
                else:
                    phase = "selection_transition"
            return {
                "active": True,
                "process": "evolution_loop",
                "arena": "continuous_evolution",
                "phase": phase,
                "model_training": model_training_active,
            }
        if open_play_active:
            return {
                "active": True,
                "process": "open_play_runner",
                "arena": "open_play",
                "phase": "open_play_validation",
                "model_training": False,
            }
        if curriculum_active:
            return {
                "active": True,
                "process": "curriculum_runner",
                "arena": "lab_play",
                "phase": "lab_generation",
                "model_training": False,
            }
        return {
            "active": False,
            "process": None,
            "arena": None,
            "phase": "idle",
            "model_training": False,
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
            "execution": runtime_status(),
            "render": self.renderer.status(),
            "research_runner": self._research_runner_status(),
        }

    def record_telemetry(self) -> bool:
        now = time.time()
        if now - self._last_telemetry_write < 1.5:
            return False
        world = self.factorio.snapshot()
        if not world.get("connected", False):
            return False
        research = self.research_data()
        progression = self.engineering_progression_data(
            world=world,
            research=research,
        )
        production = self.factorio.production_statistics(
            precision_key="5s",
            max_age_s=2.5,
        )
        execution = runtime_status()
        sample = compact_telemetry_sample(
            timestamp=now,
            world=world,
            research=research,
            progression=progression,
            production=production,
            action_context=(
                execution.get("action", {})
                if execution.get("action_active")
                else {}
            ),
        )
        append_telemetry_jsonl(TELEMETRY_LOG, sample)
        self._last_telemetry_write = now
        return True

    def sample(self) -> dict[str, Any]:
        world = self.factorio.snapshot()
        research = self.research_data()
        resource_overview = self.factorio.resource_overview()
        progression = self.engineering_progression_data(
            world=world,
            research=research,
        )
        production = self.factorio.production_statistics(
            precision_key="5s",
            max_age_s=2.5,
        )
        now = time.time()
        point = {
            "timestamp": now,
            "tick": world.get("tick"),
            "entity_count": world.get("entity_count", 0),
            "probe_latency_ms": world.get("latency_ms", 0.0),
            "factorio_connected": world.get("connected", False),
        }
        with self.history_lock:
            self.history.append(point)

        if (
            world.get("connected", False)
            and now - self._last_telemetry_write >= 1.5
        ):
            execution = runtime_status()
            sample = compact_telemetry_sample(
                timestamp=now,
                world=world,
                research=research,
                progression=progression,
                production=production,
                action_context=(
                execution.get("action", {})
                if execution.get("action_active")
                else {}
            ),
            )
            append_telemetry_jsonl(TELEMETRY_LOG, sample)
            self._last_telemetry_write = now

        return {
            "timestamp": point["timestamp"],
            "status": self.status(),
            "world": world,
            "history": self.history_data(),
            "learning": self.learning_data(),
            "run": self.active_run_data(),
            "research": research,
            "progression": progression,
            "production_plan": self.production_plan_data(
                research=research,
                progression=progression,
            ),
            "autonomy": self.autonomy_data(
                world=world,
                research=research,
                production=production,
            ),
            "resource_overview": resource_overview,
            "factory_graph": self.factory_graph_data(world=world),
            "game_graph_summary": self.game_knowledge_summary_data(
                progression=progression,
            ),
            "evolution": self.evolution_data(research=research),
            "knowledge": self.knowledge_data(),
            "datasets": self.dataset_data(),
        }

    def factory_graph_data(
        self,
        *,
        world: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        world = world if world is not None else self.factorio.snapshot()
        entities = world.get("entities", [])
        if not isinstance(entities, list):
            entities = []
        graph = build_factory_graph(entities)
        graph["tick"] = world.get("tick")
        graph["connected"] = bool(world.get("connected", False))
        return graph

    def autonomy_data(
        self,
        *,
        world: dict[str, Any] | None = None,
        research: dict[str, Any] | None = None,
        production: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        world = world if world is not None else self.factorio.snapshot()
        research = (
            research
            if research is not None
            else self.research_data()
        )
        production = (
            production
            if production is not None
            else self.factorio.production_statistics(
                precision_key="5s",
                max_age_s=2.5,
            )
        )

        metrics = research.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        intervention_state = metrics.get("interventions", {})
        instrumented = isinstance(intervention_state, dict) and bool(
            intervention_state
        )

        committed: dict[str, int] = {}
        assisted_navigation = 0
        soak_runtime = float(
            metrics.get("autonomy_soak_runtime_s", 0.0) or 0.0
        )
        if instrumented:
            candidate = (
                intervention_state.get("autonomy_window_committed")
                or intervention_state.get("post_bootstrap_committed")
                or intervention_state.get("committed")
                or {}
            )
            if isinstance(candidate, dict):
                committed = {
                    str(key): int(value or 0)
                    for key, value in candidate.items()
                    if isinstance(value, (int, float))
                }
            assisted_navigation = int(
                intervention_state.get(
                    "assisted_navigation_count",
                    metrics.get(
                        "assisted_navigation_count",
                        0,
                    ),
                )
                or 0
            )

        rates: dict[str, float] = {}
        series = production.get("series", {})
        if isinstance(series, dict):
            for item, row in series.items():
                if not isinstance(row, dict):
                    continue
                raw = row.get("produced_rate", 0.0)
                if isinstance(raw, (int, float)) and float(raw) > 0:
                    rates[str(item)] = float(raw) / 60.0

        evidence = evaluate_factory_autonomy(
            entities=world.get("entities", []),
            # `{}` would read as 'instrumented and nothing carried by
            # hand', which scores as perfect autonomy. When the run
            # carries no intervention history the honest value is None.
            interventions=committed if instrumented else None,
            production_rates_per_s=rates,
            soak_runtime_s=soak_runtime,
            assisted_navigation_count=assisted_navigation,
        ).to_dict()
        evidence["intervention_instrumented"] = instrumented
        if not instrumented:
            evidence["manual_harvest_calls"] = None
            evidence["manual_transfer_calls"] = None
            evidence["manual_logistics_calls"] = None
            evidence["manual_craft_calls"] = None
        evidence["measurement_source"] = (
            "live Factorio entities + LuaFlowStatistics + instrumented executor"
            if instrumented
            else "live Factorio entities + LuaFlowStatistics; intervention history unavailable for legacy run"
        )
        return evidence

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
        # One malformed line used to discard the whole history: the parse was
        # wrapped in a single try and the handler answered with an empty list,
        # so the panel reported zero generations and said nothing about why.
        # The loop appends to this file while the dashboard serves it, which
        # makes a half-written last line an expected state rather than a
        # corruption. The shared reader skips and counts the bad line instead.
        history_path = self.evolution_history_path
        history, history_found, history_unreadable, _ = _read_evolution_history(
            history_path
        )
        history = history[-16:]

        latest_report: dict[str, Any] | None = None
        report_dir = RUNS_DIR / "generation_reports"
        if report_dir.exists():
            try:
                report_candidates = sorted(
                    report_dir.glob("generation-*.json"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                if report_candidates:
                    loaded = json.loads(
                        report_candidates[0].read_text(encoding="utf-8")
                    )
                    if isinstance(loaded, dict):
                        latest_report = loaded
            except (OSError, json.JSONDecodeError):
                latest_report = None

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

        loop_state_path = RUNS_DIR / "evolution_loop_state.json"
        loop_state: dict[str, Any] = {}
        if loop_state_path.exists():
            try:
                loaded = json.loads(loop_state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    loop_state = loaded
            except (OSError, json.JSONDecodeError):
                loop_state = {}

        def load_learning_artifacts() -> dict[str, Any]:
            strategy: dict[str, Any] = {}
            robustness: dict[str, Any] = {}
            counterexamples: list[dict[str, Any]] = []

            for artifact_name, target in (
                ("open_play_strategy.json", "strategy"),
                ("open_play_robustness_state.json", "robustness"),
            ):
                path = RUNS_DIR / artifact_name
                if not path.exists():
                    continue
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(loaded, dict):
                    continue
                if target == "strategy":
                    strategy = loaded
                else:
                    robustness = loaded

            counterexample_path = RUNS_DIR / "counterexamples.jsonl"
            if counterexample_path.exists():
                try:
                    for raw_line in counterexample_path.read_text(
                        encoding="utf-8"
                    ).splitlines():
                        if not raw_line.strip():
                            continue
                        row = json.loads(raw_line)
                        if isinstance(row, dict):
                            counterexamples.append(row)
                except (OSError, json.JSONDecodeError):
                    counterexamples = []

            return {
                "strategy": strategy or None,
                "robustness": robustness or None,
                "counterexamples": counterexamples[-8:],
                "counterexample_count": len(counterexamples),
            }

        learning_artifacts = self._cached_artifact(
            "evolution-learning-artifacts",
            ttl_s=5.0,
            loader=load_learning_artifacts,
        )

        if isinstance(current, dict) and current:
            payload = dict(current)
            if not payload.get("champion") and champion:
                payload["champion"] = champion
            payload["validated_champion"] = validated or None
            payload["history"] = history
            payload["continuous_loop"] = loop_state or None
            payload["latest_report"] = latest_report
            payload["learning_artifacts"] = learning_artifacts
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
            # Stated so a short history reads as a short history and
            # not as a silent loss.
            "history_lines_found": history_found,
            "history_lines_unreadable": history_unreadable,
            "continuous_loop": loop_state or None,
            "latest_report": latest_report,
            "learning_artifacts": learning_artifacts,
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

    def survival_data(self, *, reports_dir: Path | None = None) -> dict[str, Any]:
        """Kaplan-Meier, hazard and cumulative incidence over the generation reports.

        Cached against the fingerprint of the report directory instead of a
        TTL: the loop writes a report every ~17 minutes, and a clock-based
        cache would eventually present a curve older than the generation being
        watched as the current one.
        """
        directory = self.generation_reports_dir if reports_dir is None else Path(reports_dir)
        key = str(directory)
        fingerprint = _generation_reports_fingerprint(directory)
        cached = self._survival_cache.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        payload = _survival_report_payload(directory)
        self._survival_cache[key] = (fingerprint, payload)
        return payload

    def discovery_data(
        self,
        *,
        history_path: Path | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """What each generation found, kept or not, read back from the history.

        Cached against the fingerprint of the history file instead of a TTL:
        the loop appends a generation every ~17 minutes, and a clock-based
        cache would present findings older than the generation on screen as
        the current ones.
        """
        path = self.evolution_history_path if history_path is None else Path(history_path)
        size = DISCOVERY_PAYLOAD_LIMIT if limit is None else int(limit)
        key = f"{path}|{size}"
        fingerprint = _evolution_history_fingerprint(path)
        cached = self._discovery_cache.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        payload = _discovery_payload(path, limit=size)
        self._discovery_cache[key] = (fingerprint, payload)
        return payload

    def dataset_data(self) -> dict[str, Any]:
        cached = self._artifact_cache.get("dataset-data")
        now = time.time()
        if cached is not None and now - cached[0] <= 5.0:
            return cached[1]

        spatial_path = RUNS_DIR / "datasets" / "spatial_demonstrations.jsonl"
        telemetry_path = RUNS_DIR / "telemetry" / "world_samples.jsonl"
        model_metadata_path = RUNS_DIR / "models" / "recurrent_world_model.json"
        spatial_model_path = RUNS_DIR / "models" / "spatial_policy.json"

        rows: list[dict[str, Any]] = []
        if spatial_path.exists():
            try:
                rows = [
                    json.loads(line)
                    for line in spatial_path.read_text().splitlines()
                    if line.strip()
                ]
            except (OSError, json.JSONDecodeError):
                rows = []

        accepted = sum(
            1
            for row in rows
            if isinstance(row, dict) and bool(row.get("accepted"))
        )

        telemetry_samples = 0
        if telemetry_path.exists():
            try:
                with telemetry_path.open(encoding="utf-8") as handle:
                    telemetry_samples = sum(1 for line in handle if line.strip())
            except OSError:
                telemetry_samples = 0

        recurrent_model: dict[str, Any] | None = None
        spatial_policy: dict[str, Any] | None = None
        if model_metadata_path.exists():
            try:
                loaded = json.loads(model_metadata_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    recurrent_model = loaded
            except (OSError, json.JSONDecodeError):
                recurrent_model = None
        if spatial_model_path.exists():
            try:
                loaded = json.loads(spatial_model_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    spatial_policy = loaded
            except (OSError, json.JSONDecodeError):
                spatial_policy = None

        payload = {
            "spatial_demonstrations": len(rows),
            "accepted_demonstrations": accepted,
            "training_ready": len(rows) >= 250,
            "minimum_training_target": 250,
            "telemetry_samples": telemetry_samples,
            "action_labeled_samples": int(
                (recurrent_model or {}).get("action_labeled_samples", 0) or 0
            ),
            "action_labeled_runs": int(
                (recurrent_model or {}).get("action_labeled_runs", 0) or 0
            ),
            "recurrent_world_model": recurrent_model,
            "spatial_policy": spatial_policy,
        }
        self._artifact_cache["dataset-data"] = (now, payload)
        return payload

    def knowledge_data(self, limit: int = 12) -> dict[str, Any]:
        cache_key = f"knowledge-data-{limit}"
        cached = self._artifact_cache.get(cache_key)
        now = time.time()
        if cached is not None and now - cached[0] <= 5.0:
            return cached[1]

        path = RUNS_DIR / "knowledge.jsonl"
        if not path.exists():
            return {
                "count": 0,
                "lessons": [],
                "verified_count": 0,
                "fallback_count": 0,
                "verified_ratio": 0.0,
            }
        try:
            lines = [line for line in path.read_text().splitlines() if line.strip()]
            records = [json.loads(line) for line in lines]
            lessons = records[-limit:]
        except (OSError, json.JSONDecodeError):
            return {"count": 0, "lessons": [], "error": "invalid knowledge log"}

        verified_count = sum(
            1
            for row in records
            if isinstance(row, dict)
            and row.get("source") == "llm_verified"
            and bool((row.get("verification") or {}).get("verified", False))
        )
        fallback_count = sum(
            1
            for row in records
            if isinstance(row, dict)
            and row.get("source") == "deterministic_fallback"
        )
        auditable = verified_count + fallback_count
        payload = {
            "count": len(lines),
            "lessons": lessons,
            "verified_count": verified_count,
            "fallback_count": fallback_count,
            "verified_ratio": (
                verified_count / auditable
                if auditable
                else 0.0
            ),
        }
        self._artifact_cache[cache_key] = (now, payload)
        return payload

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

        validated_source = engineering.get(
            "validated_achieved",
            engineering.get("achieved", []),
        )
        achieved = {
            str(goal)
            for goal in validated_source
            if isinstance(goal, str)
        }
        arena = research.get("arena", {})
        arena = arena if isinstance(arena, dict) else {}
        assumptions = {
            str(goal)
            for goal in engineering.get("planning_assumptions", [])
            if isinstance(goal, str)
        }
        if (
            arena.get("technology") == "pre_unlocked"
            and not assumptions
        ):
            assumptions = {
                "electronics_trigger",
                "lab_bootstrap",
                "lab_automation",
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

        planning_achieved = achieved | assumptions
        state = EngineeringState(
            achieved=frozenset(planning_achieved),
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
            "validated_achieved": sorted(achieved),
            "planning_assumptions": sorted(assumptions),
            "arena_mode": arena.get("mode"),
            "technology_mode": arena.get("technology"),
            "next_goal": frontier[0] if frontier else None,
            "frontier": frontier,
            "dependency_debt": list(dependency_debt),
            "stalled_attempts": stalled_attempts,
            "terminal": not frontier,
        }

    def game_knowledge_summary_data(
        self,
        *,
        progression: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        progression = (
            progression
            if progression is not None
            else self.engineering_progression_data()
        )
        next_goal = progression.get("next_goal")
        goal_id = (
            str(next_goal.get("goal_id"))
            if isinstance(next_goal, dict) and next_goal.get("goal_id")
            else ""
        )
        targets = {
            "iron_backbone": "iron-plate",
            "coal_mining": "coal",
            "copper_mining": "copper-ore",
            "steam_power": "steam-engine",
            "copper_smelting": "copper-plate",
            "electronics_trigger": "electronic-circuit",
            "lab_bootstrap": "lab",
            "automation_science": "automation-science-pack",
            "lab_automation": "automation-science-pack",
            "assembler_gears": "iron-gear-wheel",
            "electronic_circuits": "electronic-circuit",
            "logistic_science": "logistic-science-pack",
            "research_logistics": "logistic-science-pack",
            "electric_mining": "electric-mining-drill",
        }
        target = targets.get(goal_id)
        cache_key = f"game-knowledge-summary:{goal_id or 'none'}"
        cached = self._artifact_cache.get(cache_key)
        now = time.time()
        if cached is not None and now - cached[0] <= 5.0:
            return cached[1]

        payload = self.factorio.game_knowledge()
        catalog = RuntimeFactorioCatalog(payload)
        summary = catalog.summary()
        summary["frontier_goal_id"] = goal_id or None
        summary["frontier_target_item"] = target
        summary["frontier_dependency_graph"] = (
            catalog.dependency_subgraph(target)
            if target
            else None
        )
        self._artifact_cache[cache_key] = (now, summary)
        return summary

    def production_plan_data(
        self,
        *,
        research: dict[str, Any] | None = None,
        progression: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        research = research if research is not None else self.research_data()
        progression = (
            progression
            if progression is not None
            else self.engineering_progression_data(research=research)
        )
        plans = research.get("production_plans", {})
        if isinstance(plans, dict) and plans:
            key = next(reversed(plans))
            plan = plans.get(key)
            if isinstance(plan, dict):
                return {
                    "source": "validated_run_plan",
                    "plan_id": key,
                    **plan,
                }

        next_goal = progression.get("next_goal")
        goal_id = (
            str(next_goal.get("goal_id"))
            if isinstance(next_goal, dict) and next_goal.get("goal_id")
            else ""
        )
        targets = {
            "assembler_gears": ("iron-gear-wheel", 0.20),
            "automation_science": ("automation-science-pack", 0.10),
            "electronic_circuits": ("electronic-circuit", 0.20),
            "logistic_science": ("logistic-science-pack", 0.10),
        }
        target = targets.get(goal_id)
        source = "frontier"
        if target is None:
            report_dir = RUNS_DIR / "generation_reports"
            if report_dir.exists():
                try:
                    report_candidates = sorted(
                        report_dir.glob("generation-*.json"),
                        key=lambda path: path.stat().st_mtime,
                        reverse=True,
                    )
                    if report_candidates:
                        latest = json.loads(
                            report_candidates[0].read_text(encoding="utf-8")
                        )
                        report_progression = latest.get(
                            "engineering_progression",
                            {},
                        )
                        report_goal = report_progression.get("next_goal")
                        if isinstance(report_goal, dict):
                            report_goal_id = str(
                                report_goal.get("goal_id") or ""
                            )
                            report_target = targets.get(report_goal_id)
                            if report_target is not None:
                                goal_id = report_goal_id
                                next_goal = report_goal
                                target = report_target
                                source = "latest_lab_generation"
                except (OSError, json.JSONDecodeError):
                    pass
        if target is None:
            return {
                "source": source,
                "goal_id": goal_id or None,
                "factorio_data_version": FACTORIO_DATA_VERSION,
                "dag": None,
            }
        item, target_rate = target
        planner = EARLY_GAME_PRODUCTION_PLANNER
        planner_source = "static_early_game_catalog"
        try:
            runtime_payload = self.factorio.game_knowledge()
            if runtime_payload.get("connected"):
                runtime_catalog = RuntimeFactorioCatalog(runtime_payload)
                if runtime_catalog.recipe_choice(item) is not None:
                    planner = runtime_catalog.planner()
                    planner_source = "live_factorio_prototypes"
        except (OSError, RuntimeError, TypeError, ValueError):
            pass
        dag = planner.plan(item, target_rate)
        return {
            "source": source,
            "catalog_source": planner_source,
            "goal_id": goal_id,
            "goal_label": next_goal.get("label") if isinstance(next_goal, dict) else None,
            "factorio_data_version": FACTORIO_DATA_VERSION,
            "target_rate_per_s": target_rate,
            "dag": dag.to_dict(),
        }

    def world_scene(
        self,
        *,
        center_x: float | None = None,
        center_y: float | None = None,
        radius: float | None = None,
    ) -> dict[str, Any]:
        """Vector description of a world viewport, for client-side rendering.

        The PNG path re-composes and re-transfers a raster every frame, so it
        cannot pan, zoom or animate without a server round trip. This returns
        the same underlying live state as geometry instead, letting the client
        draw it at any scale.
        """
        explicit_view = (
            center_x is not None
            and center_y is not None
            and radius is not None
        )
        world = self.factorio.snapshot()
        map_context = self.factorio.map_snapshot(
            center_x=center_x if explicit_view else None,
            center_y=center_y if explicit_view else None,
            radius=radius if explicit_view else None,
        )
        prototypes = self.factorio.entity_prototypes()

        entities: list[dict[str, Any]] = []
        character: dict[str, Any] | None = None
        for entity in world.get("entities", []):
            if not isinstance(entity, dict):
                continue
            position = entity.get("position")
            if not isinstance(position, dict):
                continue
            name = str(entity.get("name", ""))
            row = {
                "name": name,
                "type": entity.get("type"),
                "x": position.get("x"),
                "y": position.get("y"),
                "direction": entity.get("direction", 0),
                "status": entity.get("status"),
                "recipe": entity.get("recipe"),
                "energy": entity.get("energy"),
                "coal_fuel": entity.get("coal_fuel"),
            }
            if name == "character":
                character = row
            else:
                entities.append(row)

        return {
            "connected": bool(
                world.get("connected", False)
                and map_context.get("connected", False)
            ),
            "tick": world.get("tick"),
            "bounds": map_context.get("bounds"),
            "center": map_context.get("center"),
            "requested_view": (
                {
                    "center_x": center_x,
                    "center_y": center_y,
                    "radius": radius,
                }
                if explicit_view
                else None
            ),
            "entities": entities,
            "entity_count": len(entities),
            "character": character,
            "resources": _as_list(map_context.get("resources")),
            "natural": _as_list(map_context.get("natural")),
            "terrain_runs": _as_list(map_context.get("terrain_runs")),
            "water_tile_count": map_context.get("water_tile_count", 0),
            "prototypes": prototypes.get("by_name", {}),
            "prototype_count": prototypes.get("count", 0),
            "error": world.get("error") or map_context.get("error"),
        }

    def render_world_frame(
        self,
        mode: str = "game",
        *,
        center_x: float | None = None,
        center_y: float | None = None,
        radius: float | None = None,
    ) -> bytes:
        explicit_view = (
            center_x is not None
            and center_y is not None
            and radius is not None
            and mode != "overview"
        )
        view_key = (
            mode,
            round(float(center_x), 2) if explicit_view else None,
            round(float(center_y), 2) if explicit_view else None,
            round(float(radius), 2) if explicit_view else None,
        )
        now = time.monotonic()
        with self._frame_cache_lock:
            cached = self._frame_cache.get(repr(view_key))
            if cached is not None and now - cached[0] <= 5.0:
                return cached[1]

        world = self.factorio.snapshot()
        map_context = self.factorio.map_snapshot(
            center_x=center_x if explicit_view else None,
            center_y=center_y if explicit_view else None,
            radius=radius if explicit_view else None,
        )
        resource_overview = self.factorio.resource_overview()
        run = self.active_run_data()
        rendered = self.renderer.render(
            world,
            run,
            map_context,
            resource_overview=resource_overview,
            mode=mode,
        )
        with self._frame_cache_lock:
            self._frame_cache[repr(view_key)] = (
                time.monotonic(),
                rendered,
            )
            if len(self._frame_cache) > 24:
                oldest = min(
                    self._frame_cache,
                    key=lambda key: self._frame_cache[key][0],
                )
                self._frame_cache.pop(oldest, None)
        return rendered

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
