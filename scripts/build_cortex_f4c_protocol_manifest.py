#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from factorio_ai_lab.cortex.causal_protocol import build_protocol_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("configs/cortex_f4c_causal_ablation_v1.json"),
    )
    args = parser.parse_args()
    payload = build_protocol_manifest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
