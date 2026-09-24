#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

PRIMARY_METRICS = (
    "autonomy_score",
    "manual_logistics_calls",
    "physical_processing_coverage",
    "fuel_starved_entities",
    "power_starved_entities",
    "external_dependencies",
    "endogenous_rate_per_s",
    "intervention_rate_per_s",
    "productive_runtime_s",
    "route_cost",
    "route_turns",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def validate_seed(seed_dir: Path, expected_commit: str | None = None) -> dict[str, Any]:
    manifest_path = seed_dir / "manifest.json"
    result_path = seed_dir / "result.json"
    if not manifest_path.exists():
        return {"seed_dir": str(seed_dir), "valid": False, "errors": ["missing manifest.json"]}
    manifest = _load(manifest_path)
    errors: list[str] = []

    if manifest.get("status") != "completed":
        errors.append(f"manifest status is {manifest.get('status')!r}")
    if manifest.get("returncode") != 0:
        errors.append(f"returncode is {manifest.get('returncode')!r}")
    if not result_path.exists():
        errors.append("missing result.json")
        return {
            "seed": manifest.get("seed"),
            "seed_dir": str(seed_dir),
            "valid": False,
            "errors": errors,
            "manifest": manifest,
        }

    result = _load(result_path)
    revision = result.get("code_revision")
    if not isinstance(revision, dict):
        errors.append("result has no code_revision object")
        revision = {}
    if revision.get("dirty") is not False:
        errors.append("result code_revision is not clean")
    manifest_commit = (manifest.get("release") or {}).get("commit")
    result_commit = revision.get("commit")
    if not manifest_commit or result_commit != manifest_commit:
        errors.append("result commit does not match manifest release")
    if expected_commit is not None and result_commit != expected_commit:
        errors.append("result commit differs from expected baseline commit")

    challenger = result.get("challenger")
    if not isinstance(challenger, dict):
        errors.append("result has no challenger")
        challenger = {}
    fitness = challenger.get("fitness")
    if not isinstance(fitness, dict):
        errors.append("challenger has no fitness")
        fitness = {}

    measurement_protocol = fitness.get("measurement_protocol")
    if not measurement_protocol:
        errors.append("fitness has no measurement_protocol")

    metrics: dict[str, float | None] = {
        name: _finite_number(fitness.get(name))
        for name in PRIMARY_METRICS
    }
    completed = fitness.get("completed_stages")
    failed = fitness.get("failed_stages")
    capabilities = fitness.get("capabilities")
    return {
        "seed": manifest.get("seed"),
        "seed_dir": str(seed_dir),
        "valid": not errors,
        "errors": errors,
        "commit": result_commit,
        "measurement_protocol": measurement_protocol,
        "generation": challenger.get("generation"),
        "run_id": challenger.get("run_id"),
        "completed_stage_count": result.get("completed_stage_count"),
        "completed_stages": list(completed) if isinstance(completed, list) else [],
        "failed_stages": list(failed) if isinstance(failed, list) else [],
        "capabilities": list(capabilities) if isinstance(capabilities, list) else [],
        "closed_loop_autonomy": fitness.get("closed_loop_autonomy"),
        "halt_cause": fitness.get("halt_cause"),
        "metrics": metrics,
        "manifest": manifest,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in records if row.get("valid")]
    summary: dict[str, Any] = {
        "schema_version": "corrected_baseline_summary_v1",
        "seed_count": len(records),
        "valid_seed_count": len(valid),
        "invalid_seed_count": len(records) - len(valid),
        "seeds": records,
        "halt_causes": dict(Counter(str(row.get("halt_cause")) for row in valid)),
        "closed_loop_successes": sum(row.get("closed_loop_autonomy") is True for row in valid),
        "metric_summary": {},
    }
    for metric in PRIMARY_METRICS:
        values = [
            row["metrics"][metric]
            for row in valid
            if row.get("metrics", {}).get(metric) is not None
        ]
        if not values:
            summary["metric_summary"][metric] = {
                "n": 0,
                "mean": None,
                "median": None,
                "min": None,
                "max": None,
            }
            continue
        summary["metric_summary"][metric] = {
            "n": len(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "min": min(values),
            "max": max(values),
        }
    stages=Counter()
    for row in valid:
        stages.update(row.get("completed_stages", []))
    summary["stage_completion_counts"] = dict(stages)
    return summary


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Corrected Baseline — Exploratory Summary",
        "",
        f"- seeds observed: {summary['seed_count']}",
        f"- valid seeds: {summary['valid_seed_count']}",
        f"- invalid seeds: {summary['invalid_seed_count']}",
        f"- closed-loop successes: {summary['closed_loop_successes']}",
        "",
        "## Seed results",
        "",
        "| Seed | Valid | Completed stages | Closed loop | Halt cause | Autonomy | Manual logistics | Physical coverage |",
        "|---:|---|---:|---|---|---:|---:|---:|",
    ]
    for row in summary["seeds"]:
        metrics=row.get("metrics", {})
        lines.append(
            "| {seed} | {valid} | {stages} | {closed} | {halt} | {autonomy} | {manual} | {coverage} |".format(
                seed=row.get("seed"),
                valid="yes" if row.get("valid") else "no",
                stages=row.get("completed_stage_count"),
                closed=row.get("closed_loop_autonomy"),
                halt=row.get("halt_cause"),
                autonomy=metrics.get("autonomy_score"),
                manual=metrics.get("manual_logistics_calls"),
                coverage=metrics.get("physical_processing_coverage"),
            )
        )
    lines += ["", "## Aggregate metrics", "", "| Metric | n | Mean | Median | Min | Max |", "|---|---:|---:|---:|---:|---:|"]
    for metric, stats in summary["metric_summary"].items():
        lines.append(
            f"| {metric} | {stats['n']} | {stats['mean']} | {stats['median']} | {stats['min']} | {stats['max']} |"
        )
    lines += [
        "",
        "## Interpretation rule",
        "",
        "With fewer than five exploratory seeds this document is a run ledger, not a statistical conclusion.",
        "Invalid seeds stay visible and must be explained; failed agent runs stay in the sample.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-commit")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args=parser.parse_args()

    seed_dirs=sorted(
        path.parent
        for path in args.root.glob("*/manifest.json")
        if path.parent.name.isdigit()
    )
    records=[validate_seed(path, args.expected_commit) for path in seed_dirs]
    summary=summarize(records)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(render_markdown(summary)+"\n",encoding="utf-8")
    if not args.json_out and not args.markdown_out:
        print(json.dumps(summary,indent=2,sort_keys=True))
    return 0 if summary["invalid_seed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
