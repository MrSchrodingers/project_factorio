from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_object(path: Path) -> dict[str, Any] | None:
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _timestamp(value: object, fallback: float) -> float:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            pass
    return fallback


def _baseline_candidates(
    state_root: Path,
    *,
    protocol: str,
    mode: str,
) -> list[dict[str, Any]]:
    base=state_root/"baseline_runs"/protocol/mode
    if not base.exists():
        return []
    rows=[]
    for manifest_path in base.glob("*/manifest.json"):
        manifest=_load_object(manifest_path)
        if manifest is None:
            continue
        seed_dir=manifest_path.parent
        result=_load_object(seed_dir/"result.json")
        status=str(manifest.get("status") or "unknown")
        fallback=manifest_path.stat().st_mtime
        when=_timestamp(
            manifest.get("finished_at") or manifest.get("started_at"),
            fallback,
        )
        rows.append({
            "kind":"baseline_seed",
            "scope":"isolated_seed",
            "protocol":protocol,
            "mode":mode,
            "seed":manifest.get("seed"),
            "status":status,
            "seed_dir":str(seed_dir),
            "runs_dir":str(seed_dir/"runs"),
            "manifest":manifest,
            "result":result,
            "_sort_time":when,
        })
    return rows


def discover_experiment_context(
    state_root: Path,
    *,
    scope: str | None = None,
) -> dict[str, Any]:
    """Resolve the dashboard evidence scope explicitly.

    The dashboard must not infer "current" from whichever global JSON happens
    to have the newest mtime. During controlled baselines the systemd unit
    declares the desired scope, and this resolver chooses only evidence inside
    that arena.
    """
    raw=(scope if scope is not None else os.environ.get(
        "FACTORIO_AI_DASHBOARD_SCOPE",
        "global",
    )).strip()

    if raw in {"", "global"}:
        return {
            "kind":"global",
            "scope":"global",
            "status":"active",
            "runs_dir":str(state_root/"runs"),
            "label":"GLOBAL / legacy research state",
            "world_source":"live_rcon",
        }

    parts=raw.split(":")
    if len(parts) != 4 or parts[0] != "baseline":
        return {
            "kind":"invalid",
            "scope":raw,
            "status":"invalid",
            "runs_dir":str(state_root/"runs"),
            "label":"INVALID DASHBOARD SCOPE",
            "world_source":"live_rcon",
            "error":"expected baseline:<protocol>:<mode>:auto",
        }

    _,protocol,mode,selector=parts
    candidates=_baseline_candidates(
        state_root,
        protocol=protocol,
        mode=mode,
    )
    if selector != "auto":
        candidates=[
            row for row in candidates
            if str(row.get("seed")) == selector
        ]
    if not candidates:
        return {
            "kind":"baseline_seed",
            "scope":"isolated_seed",
            "protocol":protocol,
            "mode":mode,
            "seed":None,
            "status":"missing",
            "runs_dir":None,
            "label":f"BASELINE {mode} / no seed evidence",
            "world_source":"live_rcon",
            "error":"no baseline manifest matched dashboard scope",
        }

    running=[row for row in candidates if row["status"] == "running"]
    selected=max(
        running or candidates,
        key=lambda row: float(row["_sort_time"]),
    )
    selected.pop("_sort_time",None)

    result=selected.get("result")
    challenger=(result or {}).get("challenger") if isinstance(result,dict) else None
    fitness=(challenger or {}).get("fitness") if isinstance(challenger,dict) else None
    research_state=_load_object(Path(selected["runs_dir"])/"research_state.json") or {}
    metrics=research_state.get("metrics") if isinstance(research_state,dict) else {}
    if not isinstance(metrics,dict):
        metrics={}

    selected["label"]=(
        f"BASELINE {mode.upper()} · seed {selected.get('seed')}"
    )
    selected["world_source"]="live_rcon"
    selected["baseline_release"]=(selected.get("manifest") or {}).get("release")
    selected["result_summary"]={
        "run_id":(challenger or {}).get("run_id") if isinstance(challenger,dict) else research_state.get("run_id"),
        "completed_stage_count":(result or {}).get("completed_stage_count") if isinstance(result,dict) else None,
        "bottleneck":(result or {}).get("bottleneck") if isinstance(result,dict) else None,
        "failed_stages":(fitness or {}).get("failed_stages",[]) if isinstance(fitness,dict) else [],
        "autonomy_score":(fitness or {}).get("autonomy_score") if isinstance(fitness,dict) else None,
        "closed_loop_autonomy":(fitness or {}).get("closed_loop_autonomy") if isinstance(fitness,dict) else None,
        "physical_processing_coverage":(fitness or {}).get("physical_processing_coverage") if isinstance(fitness,dict) else None,
        "manual_logistics_calls":(fitness or {}).get("manual_logistics_calls") if isinstance(fitness,dict) else None,
        "logistic_science_output":metrics.get("logistic_science_output"),
        "logistic_science_rate_per_s":metrics.get("logistic_science_rate_per_s"),
    }
    # Keep the public context compact. Raw result/manifest remain available in
    # the seed directory and are not shipped on every dashboard poll.
    selected.pop("result",None)
    return selected
