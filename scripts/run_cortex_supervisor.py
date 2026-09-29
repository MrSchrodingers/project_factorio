#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT=Path("/srv/factorio-ai-lab")
RUNS=ROOT/"runs"
PHASE=RUNS/"cortex_phase_state.json"
HEARTBEAT=RUNS/"cortex_supervisor.json"
PYTHON=ROOT/".venv-fle/bin/python"
POLL=float(os.environ.get("FACTORIO_AI_CORTEX_POLL_SECONDS","10"))
STOP=False
RUNNERS={
    "iron_extraction":"run_cortex_f5c_deterministic_baseline.py",
    "coal_self_sufficiency":"run_cortex_f5c_coal_self_sufficiency.py",
}

def now() -> str:
    return datetime.now(UTC).isoformat()

def load(path: Path) -> dict[str,Any]:
    value=json.loads(path.read_text())
    if not isinstance(value,dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value

def write(payload: dict[str,Any]) -> None:
    HEARTBEAT.parent.mkdir(parents=True,exist_ok=True)
    tmp=HEARTBEAT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n")
    tmp.replace(HEARTBEAT)

def refresh() -> dict[str,Any]:
    done=subprocess.run([
        str(PYTHON),str(ROOT/"scripts/cortex_phase_state.py"),
        "--state-root",str(ROOT),
        "--protocol",str(ROOT/"configs/cortex_baseline_v1.json"),
        "--write",
    ],cwd=ROOT,capture_output=True,text=True,timeout=60,check=False)
    if done.returncode!=0:
        raise RuntimeError((done.stderr or done.stdout or "phase refresh failed")[-1600:])
    return load(PHASE)

def frontier(state: dict[str,Any]) -> str | None:
    protocol=state.get("phase5_protocol")
    if not isinstance(protocol,dict):
        return None
    capabilities=protocol.get("capabilities")
    achieved=protocol.get("achieved_capabilities")
    if not isinstance(capabilities,list) or not isinstance(achieved,list):
        return None
    achieved={str(x) for x in achieved}
    for item in capabilities:
        name=str(item)
        if name not in achieved:
            return name
    return None

def git_head() -> str:
    done=subprocess.run(
        ["git","-c",f"safe.directory={ROOT}","-C",str(ROOT),"rev-parse","HEAD"],
        capture_output=True,text=True,timeout=10,check=False,
    )
    return done.stdout.strip() if done.returncode==0 else "unknown"

def runner_args(
    current: str,
    state: dict[str,Any],
) -> tuple[str,list[str]] | None:
    script=RUNNERS.get(current)
    if script is None:
        return None
    if current!="iron_extraction":
        return script,[]
    protocol=state.get("phase5_protocol")
    baseline=state.get("phase5_deterministic_baseline")
    if not isinstance(protocol,dict) or not isinstance(baseline,dict):
        return None
    partitions=protocol.get("seed_partitions")
    development=(
        partitions.get("development")
        if isinstance(partitions,dict)
        else None
    )
    attempts=baseline.get("attempts")
    if not isinstance(development,list) or not isinstance(attempts,list):
        return None
    attempted={
        int(row["seed"])
        for row in attempts
        if isinstance(row,dict)
        and isinstance(row.get("seed"),int)
        and not isinstance(row.get("seed"),bool)
    }
    for seed in development:
        if isinstance(seed,int) and not isinstance(seed,bool) and seed not in attempted:
            return script,["--seed",str(seed)]
    return None


def run(
    script: str,
    execute: bool,
    extra_args: list[str] | None=None,
) -> subprocess.CompletedProcess[str]:
    cmd=[str(PYTHON),str(ROOT/"scripts"/script)]
    cmd.extend(extra_args or [])
    if execute:
        cmd.append("--execute")
    return subprocess.run(
        cmd,cwd=ROOT,capture_output=True,text=True,timeout=900,check=False
    )

def handle(signum: int,frame: object) -> None:
    del signum,frame
    global STOP
    STOP=True

def main() -> int:
    signal.signal(signal.SIGTERM,handle)
    signal.signal(signal.SIGINT,handle)
    started=now()
    previous=load(HEARTBEAT) if HEARTBEAT.exists() else {}
    last_commit=previous.get("last_attempt_commit")
    last_frontier=previous.get("last_attempt_frontier")
    last_result=previous.get("last_result")
    while not STOP:
        commit=git_head()
        payload={
            "schema_version":"cortex_supervisor_v1",
            "active":True,
            "pid":os.getpid(),
            "started_at":started,
            "updated_at":now(),
            "commit":commit,
            "ambient_authority":"A0",
            "max_transaction_authority":"A2",
            "continuous_authority":False,
            "legacy_evolution":False,
            "last_attempt_commit":last_commit,
            "last_attempt_frontier":last_frontier,
            "last_result":last_result,
        }
        try:
            state=refresh()
            current=frontier(state)
            payload["phase"]=state.get("phase")
            payload["checkpoint"]=state.get("phase5_checkpoint")
            payload["next_checkpoint"]=state.get("phase5_next_checkpoint")
            payload["frontier"]=current
            if state.get("phase")!="F5":
                payload["status"]="idle_outside_f5"
            elif current is None:
                payload["status"]="complete"
            elif current not in RUNNERS:
                payload["status"]="active_waiting_implementation"
                payload["detail"]=f"frontier {current} has no bounded runner"
            elif runner_args(current,state) is None:
                payload["status"]="blocked_development_exhausted"
                payload["detail"]=(
                    f"frontier {current} has no remaining preregistered "
                    "development attempt"
                )
            elif (
                last_commit==commit
                and last_frontier==current
                and last_result in {"failed","blocked","rejected"}
            ):
                payload["status"]="blocked_counterexample"
                payload["detail"]="same commit/frontier already failed; no automatic retry"
            else:
                spec=runner_args(current,state)
                if spec is None:
                    raise RuntimeError("runner specification disappeared")
                script,extra_args=spec
                payload["status"]="preflight"
                payload["runner"]=script
                payload["runner_args"]=extra_args
                write(payload)
                preflight=run(script,False,extra_args)
                if preflight.returncode!=0:
                    last_commit=commit
                    last_frontier=current
                    last_result="blocked"
                    payload["status"]="blocked"
                    payload["detail"]=(preflight.stderr or preflight.stdout)[-2200:]
                else:
                    payload["status"]="executing"
                    payload["detail"]="one bounded A2 Option in progress"
                    write(payload)
                    result=run(script,True,extra_args)
                    last_commit=commit
                    last_frontier=current
                    last_result="completed" if result.returncode==0 else "failed"
                    payload["status"]="refreshing_after_execution"
                    payload["runner_returncode"]=result.returncode
                    payload["runner_output_tail"]=(result.stdout or result.stderr or "")[-3200:]
                    refresh()
            payload["last_attempt_commit"]=last_commit
            payload["last_attempt_frontier"]=last_frontier
            payload["last_result"]=last_result
        except Exception as exc:  # noqa: BLE001 - persistent supervisor
            payload["status"]="error"
            payload["error"]=f"{type(exc).__name__}: {exc}"
        write(payload)
        if not STOP:
            time.sleep(POLL)
    final=load(HEARTBEAT) if HEARTBEAT.exists() else {}
    final.update({"active":False,"status":"stopped","updated_at":now()})
    write(final)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
