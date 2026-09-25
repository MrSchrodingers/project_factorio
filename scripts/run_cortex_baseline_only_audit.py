#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def git_revision(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-c", f"safe.directory={root}", *args],
            cwd=root,
            text=True,
        ).strip()

    status=run("status","--porcelain")
    return {
        "commit":run("rev-parse","HEAD"),
        "branch":run("rev-parse","--abbrev-ref","HEAD"),
        "dirty":bool(status),
        "source":"git",
    }


def call_keywords(tree: ast.AST, name: str) -> list[set[str]]:
    rows=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):
            continue
        target=node.func
        called=(
            target.id if isinstance(target,ast.Name)
            else target.attr if isinstance(target,ast.Attribute)
            else None
        )
        if called==name:
            rows.append({kw.arg for kw in node.keywords if kw.arg})
    return rows


def cortex_legacy_imports(cortex_root: Path) -> list[str]:
    offenders=[]
    for path in sorted(cortex_root.rglob("*.py")):
        tree=ast.parse(path.read_text(encoding="utf-8"),filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                modules=[alias.name for alias in node.names]
            elif isinstance(node,ast.ImportFrom):
                modules=[node.module or ""]
            else:
                continue
            if any("curriculum_runner" in module for module in modules):
                offenders.append(str(path))
                break
    return offenders


def build(root: Path) -> dict[str, Any]:
    revision=git_revision(root)
    if revision["dirty"]:
        raise RuntimeError("refusing official G5 audit from dirty tree")

    runner_path=root/"src/factorio_ai_lab/experiments/curriculum_runner.py"
    runner_source=runner_path.read_text(encoding="utf-8")
    runner_tree=ast.parse(runner_source,filename=str(runner_path))
    run_fn=next(
        node for node in runner_tree.body
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
        and node.name=="run_curriculum"
    )
    args=[arg.arg for arg in run_fn.args.kwonlyargs]
    body=list(run_fn.body)
    if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant):
        body=body[1:]
    first_call=None
    if body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Call):
        fn=body[0].value.func
        first_call=fn.id if isinstance(fn,ast.Name) else None

    corrected=(root/"scripts/run_corrected_baseline_seed.py").read_text(encoding="utf-8")
    shell=(root/"scripts/run_curriculum.sh").read_text(encoding="utf-8")
    evolution_path=root/"src/factorio_ai_lab/experiments/evolution_loop.py"
    evolution_tree=ast.parse(evolution_path.read_text(encoding="utf-8"),filename=str(evolution_path))
    evolution_calls=call_keywords(evolution_tree,"run_curriculum")
    offenders=cortex_legacy_imports(root/"src/factorio_ai_lab/cortex")

    checks={
        "run_curriculum_requires_execution_role":"execution_role" in args,
        "guard_precedes_environment_creation":first_call=="require_legacy_baseline_role",
        "role_constant_is_baseline":'LEGACY_RUNNER_BASELINE_ROLE = "baseline"' in runner_source,
        "cli_requires_execution_role":'"--execution-role"' in runner_source and "required=True" in runner_source,
        "corrected_baseline_labels_role":'"--execution-role"' in corrected and '"baseline"' in corrected,
        "legacy_shell_labels_role":"--execution-role baseline" in shell,
        "evolution_legacy_call_labels_role":bool(evolution_calls) and all("execution_role" in row for row in evolution_calls),
        "cortex_imports_legacy_runner":not offenders,
    }
    return {
        "schema_version":"cortex_f2g5_baseline_only_audit_v1",
        "recorded_at":datetime.now(UTC).isoformat(),
        "status":"pass" if all(checks.values()) else "fail",
        "code_revision":revision,
        "legacy_runner_role":"baseline",
        "fail_closed_before_environment_creation":checks["guard_precedes_environment_creation"],
        "cortex_imports_legacy_runner":bool(offenders),
        "cortex_import_offenders":offenders,
        "world_mutation":False,
        "factorio_rcon_used":False,
        "checks":checks,
    }


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--root",type=Path,default=Path("/srv/factorio-ai-lab"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/audits/cortex_f2g5_baseline_only_enforcement.json"),
    )
    args=parser.parse_args()
    root=args.root.resolve()
    payload=build(root)
    output=args.output
    if not output.is_absolute():
        output=root/output
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_suffix(output.suffix+".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    tmp.replace(output)
    print(output)
    return 0 if payload["status"]=="pass" else 1


if __name__=="__main__":
    raise SystemExit(main())
