#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_object(path: Path) -> dict[str, Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def ensure_intervention_ledger(
    *,
    state_root: Path,
    schema_path: Path,
) -> tuple[Path,bool]:
    schema=_load_object(schema_path)
    expected_schema=schema.get("ledger_schema_version")
    protocol_id=schema.get("protocol_id")
    if not isinstance(expected_schema,str) or not expected_schema:
        raise ValueError("ledger_schema_version is required")
    if not isinstance(protocol_id,str) or not protocol_id:
        raise ValueError("protocol_id is required")

    ledger_path=state_root/"runs"/"cortex_f5_intervention_ledger.json"
    if ledger_path.exists():
        ledger=_load_object(ledger_path)
        if ledger.get("schema_version")!=expected_schema:
            raise ValueError("existing intervention ledger schema mismatch")
        if ledger.get("protocol_id")!=protocol_id:
            raise ValueError("existing intervention ledger protocol mismatch")
        interventions=ledger.get("interventions")
        if not isinstance(interventions,list):
            raise ValueError("existing intervention ledger interventions must be a list")
        return ledger_path,False

    ledger_path.parent.mkdir(parents=True,exist_ok=True)
    payload={
        "schema_version":expected_schema,
        "protocol_id":protocol_id,
        "interventions":[],
    }
    temp=ledger_path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload,indent=2,sort_keys=True)+"\n",
        encoding="utf-8",
    )
    temp.replace(ledger_path)
    return ledger_path,True


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path("/srv/factorio-ai-lab"),
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(
            "/srv/factorio-ai-lab/configs/"
            "cortex_f5_intervention_ledger_schema_v1.json"
        ),
    )
    args=parser.parse_args()
    path,created=ensure_intervention_ledger(
        state_root=args.state_root.resolve(),
        schema_path=args.schema.resolve(),
    )
    print(json.dumps({
        "path":str(path),
        "created":created,
    },sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
