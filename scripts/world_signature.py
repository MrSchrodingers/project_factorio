#!/usr/bin/env python3
"""Read the terrain signature of a running Factorio world over RCON.

Strictly read-only: the command sent is a single `find_entities_filtered`
sweep for resource entities plus a read of `map_gen_settings`. It creates,
moves and destroys nothing, and it does not take the world lease, so it is
safe to run while an experiment owns the instance.

Usage:
    PYTHONPATH=src .venv-fle/bin/python scripts/world_signature.py --port 27000

Exit codes: 0 signature captured, 2 capture failed, 3 signature differs from
the value passed in --expect.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from factorio_ai_lab.learning.map_suite import (
    DEFAULT_SCAN_RADIUS,
    PATCH_CELL_SIZE,
    world_fingerprint,
    world_signature_command,
)


def capture(
    *,
    host: str,
    port: int,
    password: str,
    radius: int,
    cell_size: int,
) -> dict[str, object]:
    from factorio_rcon import RCONClient

    client = RCONClient(host, port, password)
    try:
        raw = client.send_command(
            world_signature_command(radius=radius, cell_size=cell_size)
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    if not raw:
        raise RuntimeError("RCON returned an empty payload")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("RCON payload was not an object")
    return world_fingerprint(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27000)
    parser.add_argument("--password", default="factorio")
    parser.add_argument("--radius", type=int, default=DEFAULT_SCAN_RADIUS)
    parser.add_argument("--cell-size", type=int, default=PATCH_CELL_SIZE)
    parser.add_argument("--label", default=None, help="map_id to tag the capture with")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--expect", default=None, help="fail if the signature differs")
    parser.add_argument("--quiet", action="store_true", help="print the signature only")
    args = parser.parse_args()

    try:
        fingerprint = capture(
            host=args.host,
            port=args.port,
            password=args.password,
            radius=args.radius,
            cell_size=args.cell_size,
        )
    # The RCON client raises its own exception hierarchy rooted at Exception;
    # a capture tool turns any failure into a non-zero exit, never a traceback.
    except Exception as exc:  # noqa: BLE001
        print(f"capture_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    fingerprint["endpoint"] = f"{args.host}:{args.port}"
    if args.label:
        fingerprint["map_id"] = args.label

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(fingerprint, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    signature = str(fingerprint["world_signature"])
    if args.quiet:
        print(signature)
    else:
        print(json.dumps(fingerprint, indent=2, sort_keys=True))

    if args.expect is not None and args.expect != signature:
        print(
            f"signature_mismatch: expected {args.expect}, observed {signature}",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
