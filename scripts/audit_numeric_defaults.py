#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

HIGH_RISK_TOKENS = {
    "rate",
    "throughput",
    "fitness",
    "score",
    "reward",
    "output",
    "production",
    "consumption",
    "power",
    "fuel",
    "starved",
    "count",
    "amount",
    "duration",
    "coverage",
    "autonomy",
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    key: str | None
    fallback: str
    expression: str
    high_risk: bool


def _numeric(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _risk(key: str | None, expression: str) -> bool:
    haystack = f"{key or ''} {expression}".lower()
    return any(token in haystack for token in HIGH_RISK_TOKENS)


def scan_file(path: Path, *, root: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) >= 2
            and _numeric(node.args[1])
        ):
            key = _key(node.args[0])
            expr = ast.unparse(node)
            findings.append(
                Finding(
                    path=str(path.relative_to(root)),
                    line=node.lineno,
                    kind="mapping_get_numeric_default",
                    key=key,
                    fallback=ast.unparse(node.args[1]),
                    expression=expr,
                    high_risk=_risk(key, expr),
                )
            )
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            if len(node.values) == 2 and _numeric(node.values[1]):
                expr = ast.unparse(node)
                findings.append(
                    Finding(
                        path=str(path.relative_to(root)),
                        line=node.lineno,
                        kind="or_numeric_fallback",
                        key=None,
                        fallback=ast.unparse(node.values[1]),
                        expression=expr,
                        high_risk=_risk(None, expr),
                    )
                )
    return findings


def scan(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".tmp", "__pycache__", ".venv", ".venv-fle"} for part in path.parts):
            continue
        findings.extend(scan_file(path, root=root))
    return sorted(findings, key=lambda row: (not row.high_risk, row.path, row.line, row.kind))


def render_markdown(findings: list[Finding], root: Path) -> str:
    high = [row for row in findings if row.high_risk]
    lines = [
        "# F1 Numeric Default Audit",
        "",
        f"- scanned_root: {root}",
        f"- findings_total: {len(findings)}",
        f"- high_risk_candidates: {len(high)}",
        "",
        "This is a static audit of places where absent values can be replaced by a numeric fallback.",
        "A finding is not automatically a bug. Each high-risk candidate must be reviewed against",
        "the measurement contract; the purpose is to make silent missing-to-number substitutions",
        "enumerable and reviewable.",
        "",
        "## High-risk candidates",
        "",
        "| Path | Line | Kind | Key | Fallback |",
        "|---|---:|---|---|---:|",
    ]
    for row in high:
        lines.append(
            f"| {row.path} | {row.line} | {row.kind} | {row.key or ''} | {row.fallback} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "High-risk means the expression mentions a metric-like token. It does not mean the fallback",
        "is wrong: counters that are defined as zero when absent are legitimate. The review must",
        "distinguish those from unread/failed measurements, which must use EvidenceStatus.missing",
        "or EvidenceStatus.invalid instead of a number.",
        "",
        "## Machine-readable output",
        "",
        "The JSON output generated with --json is the exhaustive list and should be attached to",
        "baseline evidence under runs/audits/.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("src"))
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    findings = scan(root)
    payload = {
        "schema_version": "numeric_default_audit_v1",
        "root": str(root),
        "finding_count": len(findings),
        "high_risk_count": sum(row.high_risk for row in findings),
        "findings": [asdict(row) for row in findings],
    }

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(findings, root) + "\n", encoding="utf-8")
    if not args.json and not args.markdown:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
