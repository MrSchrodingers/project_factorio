#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

import gym

from factorio_ai_lab.integrations.fle import TransactionalFLEExecutor, list_environments


def main() -> int:
    parser = argparse.ArgumentParser(description="FLE transactional smoke test.")
    parser.add_argument("--env-id", default="iron_ore_throughput")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27000)
    parser.add_argument("--seed", type=int, default=123)
    args = parser.parse_args()

    os.environ["FACTORIO_SERVER_ADDRESS"] = args.host
    os.environ["FACTORIO_SERVER_PORT"] = str(args.port)

    available = list_environments()
    if args.env_id not in available:
        raise ValueError(f"unknown environment {args.env_id!r}")

    env = gym.make(args.env_id, run_idx=0)
    executor = TransactionalFLEExecutor(env)

    try:
        executor.reset(seed=args.seed)

        accepted = executor.execute(
            "print(inspect_inventory())",
            accept=lambda step: not bool(step.info.get("error_occurred")),
        )
        if not accepted.accepted or accepted.candidate_game_state is None:
            raise RuntimeError("accepted probe did not yield a committed game state")

        checkpoint = executor.game_state

        rejected = executor.execute(
            "print(get_entities())",
            accept=lambda _: False,
        )
        if rejected.accepted:
            raise RuntimeError("rejected probe was unexpectedly committed")
        if executor.game_state != checkpoint:
            raise RuntimeError("rollback did not restore the committed checkpoint")

        print("environment=", args.env_id)
        print("registered_environments=", len(available))
        print("accepted_reward=", accepted.reward)
        print("accepted_info_keys=", ",".join(sorted(accepted.info)))
        print("rollback_verified=true")
        return 0
    finally:
        executor.close()


if __name__ == "__main__":
    raise SystemExit(main())
