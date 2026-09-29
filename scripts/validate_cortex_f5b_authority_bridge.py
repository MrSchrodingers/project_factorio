#!/usr/bin/env python3
"""Audit the F5-B bounded authority bridge without mutating Factorio."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorio_ai_lab.paths import RUNS_DIR, code_revision
from factorio_ai_lab.runtime import WORLD_LEASE_STATE

SCHEMA_VERSION="cortex_f5b_authority_bridge_audit_v1"
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT=RUNS_DIR/"audits"/"cortex_f5b_authority_bridge.json"
INTERVENTION_LEDGER=RUNS_DIR/"cortex_f5_intervention_ledger.json"
TEST_FILES=(
    "tests/test_cortex_f5_authority.py",
    "tests/test_cortex_option_grant_ledger.py",
    "tests/test_cortex_option_execute.py",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load(path: Path) -> dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def evolution_service_state() -> dict[str,str]:
    def run(action: str) -> str:
        done=subprocess.run(
            ["systemctl",action,"factorio-ai-evolution"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        text=(done.stdout or done.stderr or "").strip()
        return text.splitlines()[0] if text else f"returncode:{done.returncode}"
    return {"active":run("is-active"),"enabled":run("is-enabled")}


def phase_state() -> dict[str,Any]:
    path=RUNS_DIR/"cortex_phase_state.json"
    return _load(path) if path.exists() else {}


def world_lease_state() -> dict[str,Any]:
    if not WORLD_LEASE_STATE.exists():
        return {"status":"absent"}
    return _load(WORLD_LEASE_STATE)


def intervention_state() -> dict[str,Any]:
    return _load(INTERVENTION_LEDGER)


def run_targeted_tests() -> dict[str,Any]:
    env=dict(os.environ)
    env["PYTHONPATH"]=str(ROOT/"src")
    command=[sys.executable,"-m","pytest","-q",*TEST_FILES]
    done=subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    output=((done.stdout or "")+"\n"+(done.stderr or "")).strip()
    return {
        "command":command,
        "returncode":done.returncode,
        "passed":done.returncode==0,
        "output_tail":output[-6000:],
    }


def build_audit() -> dict[str,Any]:
    revision=code_revision()
    phase=phase_state()
    protocol=phase.get("phase5_protocol")
    if not isinstance(protocol,dict):
        protocol={}
    evolution=evolution_service_state()
    lease=world_lease_state()
    intervention=intervention_state()
    interventions=intervention.get("interventions")
    if not isinstance(interventions,list):
        interventions=[]
    authority_schema=_load(ROOT/"configs"/"cortex_f5_authority_schema_v1.json")
    levels=authority_schema.get("levels")
    if not isinstance(levels,dict):
        levels={}
    a2=levels.get("A2")
    if not isinstance(a2,dict):
        a2={}
    a6=levels.get("A6")
    if not isinstance(a6,dict):
        a6={}

    tests=run_targeted_tests()
    source_paths={
        "f5_authority":"src/factorio_ai_lab/cortex/f5_authority.py",
        "grant_ledger":"src/factorio_ai_lab/cortex/grant_ledger.py",
        "option_execute":"src/factorio_ai_lab/cortex/option_execute.py",
        "runtime_lease":"src/factorio_ai_lab/runtime.py",
        "authority_schema":"configs/cortex_f5_authority_schema_v1.json",
        "audit_script":"scripts/validate_cortex_f5b_authority_bridge.py",
    }
    source_hashes={
        key:_sha256(ROOT/path)
        for key,path in source_paths.items()
    }
    checks={
        "clean_committed_source":revision.get("dirty") is False,
        "phase5a_protocol_validated":(
            phase.get("phase")=="F5"
            and phase.get("phase5_checkpoint")=="F5-A"
            and phase.get("phase5_next_checkpoint")=="F5-B"
            and protocol.get("validated") is True
        ),
        "ambient_authority_is_a0":protocol.get("authority_level")=="A0",
        "world_mutation_not_authorized":(
            protocol.get("world_mutation_authorized") is False
        ),
        "continuous_authority_off":(
            protocol.get("continuous_authority") is False
            and authority_schema.get("continuous_authority_allowed") is False
            and a6.get("allowed") is False
        ),
        "a2_is_exactly_one_transactional_option":(
            a2.get("scope")=="single_transactional_option"
            and a2.get("max_executions")==1
        ),
        "policy_self_grant_forbidden":(
            authority_schema.get("policy_may_self_grant_authority") is False
        ),
        "evolution_inactive_disabled":(
            evolution.get("active")=="inactive"
            and evolution.get("enabled")=="disabled"
        ),
        "no_active_world_lease":lease.get("status")!="active",
        "intervention_ledger_empty":len(interventions)==0,
        "targeted_authority_tests_pass":tests["passed"] is True,
    }
    return {
        "schema_version":SCHEMA_VERSION,
        "status":"pass" if all(checks.values()) else "failed",
        "checkpoint":"F5-B",
        "authority":{
            "ambient_level":"A0",
            "bounded_grant_level":"A2",
            "max_executions":1,
            "continuous_authority":False,
            "policy_may_self_grant_authority":False,
        },
        "world_mutation":False,
        "world_lease_acquired":False,
        "grant_issued":False,
        "option_executed_live":False,
        "live_canary_required_for_f5b":False,
        "next_checkpoint":"F5-C",
        "code_revision":revision,
        "phase_state_before": {
            "phase":phase.get("phase"),
            "phase5_checkpoint":phase.get("phase5_checkpoint"),
            "phase5_next_checkpoint":phase.get("phase5_next_checkpoint"),
        },
        "evolution":evolution,
        "world_lease_state":lease,
        "intervention_count":len(interventions),
        "tests":tests,
        "source_paths":source_paths,
        "source_hashes":source_hashes,
        "checks":checks,
        "recorded_at":utc_now(),
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--artifact",type=Path,default=DEFAULT_ARTIFACT)
    args=parser.parse_args()
    artifact=args.artifact.resolve()
    if artifact.exists():
        raise FileExistsError(
            f"canonical F5-B audit already exists: {artifact}"
        )
    payload=build_audit()
    if payload["status"]!="pass":
        print(json.dumps(payload,indent=2,sort_keys=True))
        return 2
    artifact.parent.mkdir(parents=True,exist_ok=True)
    temp=artifact.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload,indent=2,sort_keys=True)+"\n",
        encoding="utf-8",
    )
    temp.replace(artifact)
    print(artifact)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
